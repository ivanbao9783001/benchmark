"""Read-only task panorama derived from frozen inputs, results and status.

The summary is a projection: it never writes task state and never replaces the
authoritative service/state/journal artifacts. Callers use it to see every
phase, its evidence and the core values without parsing token streams.
"""
import re
from pathlib import Path
from .json_io import load_json
from .protocol import ProtocolError, envelope, now

SCHEMA_VERSION = 'task-summary/1'
PHASES = ('prepare-inputs', 'package-runtime', 'draft-review', 'freeze-inputs',
          'check-ready', 'publish-task', 'start-task', 'deploy', 'eval',
          'release', 'stop-runtime')
ALIASES = {'freeze': 'freeze-inputs', 'freeze_inputs': 'freeze-inputs',
           'deploy-service': 'deploy', 'evaluation': 'eval', 'stop': 'stop-runtime'}
# Actions that never run a phase; absence is expected, not an unknown outcome.
NOT_APPLICABLE = {'eval_only': ('deploy', 'release'), 'deploy_only': ('eval',),
                  # deploy_and_eval assembles plan/bindings itself, so it never
                  # runs the prepare-inputs phase.
                  'deploy_and_eval': ('prepare-inputs',)}
MAX_JSON = 1048576
MAX_PROGRESS = 262144
MODEL_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]*$')
MODEL_ID_UNSAFE = re.compile(r'[^A-Za-z0-9_-]')


def _normalize_model_id(name):
    """Derive a schema-safe technical id from a free-form served model name.

    Existing-service evaluation only carries the display name. Dots are legal
    on the wire but not in deployment ids, so the summary keeps the display
    name in ``model`` and derives a stable id for ``model_id``.
    """
    value = MODEL_ID_UNSAFE.sub('-', name).lstrip('-_')
    return value if value and MODEL_ID.match(value) else None


def _read(path, errors=None):
    try:
        return load_json(path, MAX_JSON)
    except Exception:
        if errors is not None:
            errors.append(path.name + ' unreadable')
        return None


def _tail(path, limit):
    try:
        size = path.stat().st_size
        with path.open('rb') as stream:
            if size > limit:
                stream.seek(-limit, 2)
            data = stream.read()
        return data.decode('utf-8', 'replace')
    except OSError:
        return ''


def _progress(path):
    """Parse the PHASE heartbeat; the last line per phase wins."""
    latest, attempts = {}, {}
    for line in _tail(path, MAX_PROGRESS).splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0] != 'PHASE':
            continue
        name = ALIASES.get(parts[1], parts[1])
        try:
            rc, elapsed = int(parts[2]), float(parts[3])
        except ValueError:
            continue
        latest[name] = dict(rc=rc, elapsed_seconds=elapsed,
                            time=parts[4] if len(parts) > 4 else None)
        attempts[name] = attempts.get(name, 0) + 1
    return latest, attempts


def _frozen(root, errors):
    candidates = [p for p in root.glob('frozen*') if p.is_dir() and (p/'compiled.json').is_file()]
    if not candidates:
        # Evaluating an existing service freezes plan/bindings/review without
        # compiling a deployment topology; only non-deploy plans qualify here.
        for path in root.glob('frozen*'):
            if not path.is_dir() or not (path/'plan.json').is_file():
                continue
            plan = _read(path/'plan.json')
            if isinstance(plan, dict) and plan.get('intent') in ('external_eval', 'service_eval'):
                candidates.append(path)
    if not candidates:
        if any(p.is_dir() for p in root.glob('frozen*')):
            errors.append('frozen directory is incomplete')
        return None, {}
    folder = max(candidates, key=lambda p: p.stat().st_mtime)
    values = {}
    for name in ('plan.json', 'bindings.json', 'compiled.json', 'review.json'):
        path = folder/name
        if not path.is_file():
            if name != 'compiled.json':
                errors.append('frozen/' + name + ' missing')
            continue
        value = _read(path, errors)
        if isinstance(value, dict):
            values[name] = value
    return folder, values


def _compact(value):
    return {key: item for key, item in value.items()
            if item is not None and item != [] and item != {}}


def _task_identity(plan, compiled, bindings, top_input, errors):
    deployment = plan.get('deployment') or {}
    model = deployment.get('model') or {}
    # Existing-service evaluation names the model on the service, not a deployment.
    served = (plan.get('service') or {}).get('model')
    served = served if isinstance(served, str) and served else None
    topology = compiled.get('topology') or {}
    service_endpoint = (plan.get('service') or {}).get('endpoint')
    service_endpoint = service_endpoint if isinstance(service_endpoint, str) and service_endpoint else None
    groups = topology.get('groups') or {}
    roles = {item.get('role') for item in deployment.get('instances') or []
             if isinstance(item, dict)}
    runtime = deployment.get('runtime') or {}
    nodes = []
    for slot, binding in (bindings.get('nodes') or {}).items():
        if not isinstance(binding, dict):
            continue
        group = groups.get(slot) or {}
        container = binding.get('container') or {}
        group_container = group.get('container') or {}
        network = binding.get('network')
        nodes.append(_compact(dict(
            node=slot, host=binding.get('host'), machine_id=binding.get('machine_id'),
            container_id=container.get('id') or group_container.get('id'),
            image=container.get('image') or group_container.get('image') or group.get('image'),
            self=bool(container.get('self') or group.get('self')),
            network=network.get('ip') if isinstance(network, dict) else network,
            npu_ids=group.get('npu_ids'), ports=group.get('ports'),
            device_map=group.get('device_map'), health_urls=group.get('health_urls'))))
    if not model.get('id') and not compiled.get('model') and not served:
        errors.append('model identity not derivable')
    # ``model`` is the effective served name; ``model_id`` is always the
    # schema-safe technical id and never the free-form display name.
    model_id = compiled.get('model_id') or model.get('id')
    if not model_id and served:
        model_id = _normalize_model_id(served)
    name = compiled.get('model') or model.get('served_name') or served or model_id
    if model_id is not None and not MODEL_ID.match(str(model_id)):
        errors.append('model_id is not a valid technical id: ' + str(model_id))
    return _compact(dict(
        id=plan.get('task_id'), action=top_input.get('action'),
        topology=('pd' if roles & {'prefill', 'decode'} else 'colocated' if roles else None),
        runtime_mode=runtime.get('mode'),
        deployment_id=deployment.get('id'),
        model=name,
        model_id=model_id,
        endpoint=topology.get('endpoint') or service_endpoint,
        connector=(deployment.get('kv_transfer') or {}).get('connector'),
        node_count=len(groups) or len(nodes) or None,
        nodes=nodes))


def _operation(path, root, data):
    if 'case_id' in data:
        kind = 'eval'
    elif 'resources' in data or ('cleanup_status' in data and 'endpoint' not in data):
        kind = 'release'
    elif 'endpoint' in data or 'containers' in data:
        kind = 'deploy'
    else:
        kind = 'operation'
    if 'execution_status' in data:
        status = {'SUCCEEDED': 'succeeded', 'FAILED': 'failed',
                  'CANCELLED': 'cancelled'}.get(data['execution_status'], 'unknown')
    elif data.get('cleanup_status') == 'VERIFIED':
        status = 'succeeded'
    elif data.get('cleanup_status'):
        status = 'unverified'
    else:
        status = 'unknown'
    summary = {}
    keys = ('service_id', 'endpoint', 'model', 'model_id', 'case_id',
            'evaluation_type', 'execution_status', 'evidence_status',
            'performance_validity', 'accuracy_validity', 'cleanup_status',
            'error_code', 'phase')
    for key in keys:
        if data.get(key) is not None:
            summary[key] = data[key]
    if isinstance(data.get('containers'), (dict, list)):
        summary['containers'] = len(data['containers'])
    resources = data.get('resources')
    if isinstance(resources, dict):
        summary['nodes'] = len(resources)
        summary['verified_nodes'] = sum(
            1 for item in resources.values()
            if isinstance(item, dict) and item.get('cleanup_status') == 'VERIFIED')
    return dict(operation_id=path.parent.name, kind=kind, status=status,
                time=data.get('time'), summary=summary,
                evidence_ref=path.relative_to(root).as_posix())


def _operations(root, errors):
    folder = root/'results'
    rows = []
    if not folder.is_dir():
        return rows
    for path in sorted(folder.glob('*/stage-result.json')):
        data = _read(path, errors)
        if isinstance(data, dict):
            rows.append(_operation(path, root, data))
        elif data is not None:
            errors.append(path.parent.name + '/stage-result.json is not an object')
    rows.sort(key=lambda item: (str(item.get('time') or ''), item['operation_id']))
    return rows


def _phases(root, frozen_ready, operations, action=None, notes=None):
    latest, attempts = _progress(root/'progress.log')
    skipped = NOT_APPLICABLE.get(action or '', ())
    artifact = {}
    if frozen_ready:
        artifact['freeze-inputs'] = 'succeeded'
    for kind in ('deploy', 'eval', 'release'):
        rows = [item for item in operations if item['kind'] == kind]
        if rows:
            artifact[kind] = rows[-1]['status']
    phases = []
    for name in PHASES:
        row = latest.get(name)
        detail = dict(source='none')
        if row:
            status = 'succeeded' if row['rc'] == 0 else 'failed'
            detail = dict(source='progress.log', rc=row['rc'],
                          elapsed_seconds=row['elapsed_seconds'],
                          time=row['time'], attempts=attempts.get(name, 1))
        elif name in artifact:
            status = artifact[name]
            detail = dict(source='artifact')
        elif name in skipped:
            status = 'not_applicable'
            detail = dict(source='action')
        else:
            status = 'unknown'
        if name in ('deploy', 'eval', 'release'):
            detail['operations'] = [item['operation_id'] for item in operations
                                    if item['kind'] == name]
        phases.append(dict(name=name, status=status, detail=_compact(detail)))
    # The phase list is a fixed contract. Names outside it are a skill-side
    # vocabulary error, not new phases; surface them without inflating phases.
    extra = sorted(name for name in latest if name not in PHASES)
    if extra and notes is not None:
        shown = ', '.join(extra[:4])
        notes.append('progress.log uses non-canonical phase names: ' + shown +
                     (', ...' if len(extra) > 4 else ''))
    return phases


def summarize(binding, task, *, status=None, status_error=None):
    """Build the summary envelope. ``status`` is the get-status response when reachable."""
    root = Path(binding['task_root'])
    if not root.is_dir():
        raise ProtocolError('INVALID_INPUT: task delivery root')
    errors = []
    top_input = _read(root/'top_input.json', errors) or {}
    folder, frozen = _frozen(root, errors)
    plan = frozen.get('plan.json') or {}
    compiled = frozen.get('compiled.json') or {}
    bindings = frozen.get('bindings.json') or {}
    # top-input/2 nests the action under ``task``; older request fixtures keep it
    # at the top level. Normalize once so identity and phases agree.
    nested = top_input.get('task')
    if not top_input.get('action') and isinstance(nested, dict) and nested.get('action'):
        top_input = {**top_input, 'action': nested['action']}
    # Evaluation-only tasks may not record an action; their plan intent decides it.
    if not top_input.get('action') and plan.get('intent') in ('external_eval', 'service_eval'):
        top_input = {**top_input, 'action': 'eval_only'}
    identity = _task_identity(plan, compiled, bindings, top_input, errors)
    identity['id'] = identity.get('id') or task
    identity['frozen'] = _compact(dict(directory=folder.name if folder else None,
                                       files=sorted(frozen)))
    operations = _operations(root, errors)

    service = evaluation = cleanup = None
    deploys = [item for item in operations if item['kind'] == 'deploy']
    evals = [item for item in operations if item['kind'] == 'eval']
    releases = [item for item in operations if item['kind'] == 'release']
    if deploys:
        last = deploys[-1]
        if last['summary'].get('service_id') or last['summary'].get('endpoint'):
            service = _compact(dict(service_id=last['summary'].get('service_id'),
                                    endpoint=last['summary'].get('endpoint'),
                                    model=last['summary'].get('model'),
                                    status=last['status'], evidence_ref=last['evidence_ref']))
    if evals:
        last = evals[-1]
        if last['summary'].get('case_id'):
            evaluation = _compact(dict(case_id=last['summary'].get('case_id'),
                                       evaluation_type=last['summary'].get('evaluation_type'),
                                       status=last['status'],
                                       evidence_status=last['summary'].get('evidence_status'),
                                       performance_validity=last['summary'].get('performance_validity'),
                                       accuracy_validity=last['summary'].get('accuracy_validity'),
                                       evidence_ref=last['evidence_ref']))
    cleanup_source = (releases or deploys or evals)
    if cleanup_source:
        last = cleanup_source[-1]
        cleanup = _compact(dict(status=last['summary'].get('cleanup_status'),
                                source=last['kind'], operation_id=last['operation_id'],
                                evidence_ref=last['evidence_ref']))

    if status is not None:
        state = _compact(dict(task_state=status.get('task_state'),
                              task_version=status.get('task_version'),
                              operation_id=status.get('operation_id'),
                              offline=status.get('offline'),
                              source='get-status'))
    elif status_error:
        state = dict(task_state=None, task_version=None, operation_id=None,
                     source='unavailable', error=status_error)
    else:
        state = dict(task_state=None, task_version=None, operation_id=None, source='none')
    if status_error:
        errors.append('get-status unavailable: ' + str(status_error.get('code', 'UNKNOWN_OUTCOME')))
    if not folder:
        errors.append('frozen inputs not found under task_root')
    if not (root/'progress.log').is_file():
        errors.append('progress.log absent; phases without artifacts show unknown')

    response = envelope(task, 0, 'OK')
    response['schema_version'] = SCHEMA_VERSION
    response['summary'] = dict(
        task=identity, state=state,
        phases=_phases(root, bool(folder), operations, top_input.get('action'), errors),
        operations=operations, service=service, evaluation=evaluation,
        cleanup=cleanup, limitations=errors[:8], updated_at=now())
    return response
