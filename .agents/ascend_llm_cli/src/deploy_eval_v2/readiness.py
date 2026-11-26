"""Offline readiness checks. Never connect to hosts or start workloads."""
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from .compiler import verify_frozen
from .device_map import parse_chip_logic_map, validate_device_map
from .publication import install_archive
from .publication_cli import task_binding,publication_targets
from .protocol import ProtocolError,envelope
from .remote_guard import resolve_guard_root
from .topology import node_physical_devices
from .identity import resolve_identity


def local_machine_id():
    """The control station's identity: /etc/machine-id or the hostname fallback."""
    try:
        return resolve_identity()['machine_id']
    except ProtocolError:
        return None


def control_is_posix():
    return os.name=='posix'


def read_local_chip_logic_map():
    """Probe this node's NPU numbering; None when npu-smi is absent or unusable."""
    try:
        result=subprocess.run(['npu-smi','info','-m'],capture_output=True,text=True,timeout=30)
    except (OSError,subprocess.SubprocessError):
        return None
    if result.returncode:
        return None
    try:
        observed=parse_chip_logic_map(result.stdout)
    except ValueError:
        return None
    return observed or None


def task_root_writable(path):
    """The control-side delivery root must be creatable without creating it."""
    target=Path(path)
    while not target.exists() and target!=target.parent:
        target=target.parent
    return target.is_dir() and os.access(target,os.W_OK|os.X_OK)


def self_container_nodes(bound,slots):
    return [slot for slot in slots
            if isinstance(bound['nodes'][slot].get('container'),dict) and
            bound['nodes'][slot]['container'].get('self') is True]


def check_ready(request,config):
    fields={'task_ref','runtime_archive','input_directory','runtime_sha256','bundle_sha256'}
    if not isinstance(request,dict) or set(request)!=fields:
        raise ProtocolError('INVALID_INPUT: readiness request')
    task=request['task_ref']
    binding=task_binding(config,task,delivery=True)
    values=verify_frozen(request['input_directory'],request['bundle_sha256'],request['runtime_sha256'])
    plan,bound=values['plan.json'],values['bindings.json']
    if plan['task_id']!=task:
        raise ProtocolError('INVALID_INPUT: readiness task mismatch')
    targets=publication_targets(values,binding)
    with tempfile.TemporaryDirectory(prefix='v2-readiness-') as folder:
        install_archive(request['runtime_archive'],Path(folder)/'runtime',request['runtime_sha256'])
    blockers=[]
    def require(condition,code):
        if not condition and code not in blockers:
            blockers.append(code)
    require(task_root_writable(binding['task_root']),'TASK_ROOT_NOT_WRITABLE')
    remote=[target for _,target in targets if target.get('self') is not True]
    require(shutil.which('ssh') is not None or not remote,'CONTROL_SSH_MISSING')
    for _,target in targets:
        python=target.get('python','/usr/bin/python3')
        require(isinstance(python,str) and python.startswith('/'),'ABSOLUTE_REMOTE_PYTHON_REQUIRED')
    def machine(item):
        value=item.get('machine_id','')
        return value if isinstance(value,str) and re.fullmatch('[a-f0-9]{32}',value) else None
    evaluation=None
    if plan['intent']=='external_eval':
        evaluation=plan
        tester=bound['testers'][plan['test_binding']]
        require(machine(tester) is not None,'TESTER_MACHINE_ID_REQUIRED')
        require(tester['host']==binding['host'],'RUNTIME_MUST_USE_TESTER_HOST')
    elif plan['intent']=='deploy':
        groups=values['compiled.json']['topology']['groups']
        slots={g['node'] for g in groups.values()}
        local={g['node'] for g in groups.values() if (g['host'],g['root'])==(binding['host'],binding['root'])}
        require(len(local)==1,'RUNTIME_NODE_BINDING_REQUIRED')
        identities=[machine(bound['nodes'][slot]) for slot in slots]
        require(all(identities),'NODE_MACHINE_ID_REQUIRED')
        require(len(set(identities))==len(identities),'NODE_MACHINE_IDS_MUST_BE_DISTINCT')
        hosts=[next(g['host'] for g in groups.values() if g['node']==slot) for slot in slots]
        require(len(set(hosts))==len(hosts),'DUPLICATE_NODE_HOST')
        for slot in slots:
            guard_root=bound['nodes'][slot].get('guard_root')
            if guard_root is None:
                continue
            try:
                resolve_guard_root(bound['nodes'][slot],
                                   next(g['root'] for g in groups.values() if g['node']==slot))
            except ProtocolError:
                require(False,'GUARD_ROOT_INVALID')
        self_slots=self_container_nodes(bound,slots)
        if self_slots or binding.get('self') is True:
            existing=(plan['deployment'].get('runtime',{}).get('mode','owned_container')=='existing_container')
            require(existing,'SELF_CONTAINER_REQUIRES_EXISTING')
            require(len(self_slots)==1,'SELF_NODE_COUNT')
            require(len(self_slots)==1 and set(self_slots)==local,'SELF_NODE_MUST_BE_CONTROL')
            require(binding.get('self') is True,'CONTROL_ENTRY_SELF_REQUIRED')
            require(control_is_posix(),'SELF_CONTROL_REQUIRES_POSIX')
            if control_is_posix() and len(self_slots)==1:
                local=local_machine_id()
                if local is not None:
                    require(local==machine(bound['nodes'][self_slots[0]]),'SELF_MACHINE_ID_MISMATCH')
        declared={slot:bound['nodes'][slot]['container']['device_id_map'] for slot in slots
                  if isinstance(bound['nodes'][slot].get('container'),dict)
                  and 'device_id_map' in bound['nodes'][slot]['container']}
        deployment=plan.get('deployment',{})
        existing=(deployment.get('runtime',{}).get('mode','owned_container')=='existing_container')
        if existing:
            # One container per host: every node carries its own container. A
            # remote node also needs its container-internal interpreter; the
            # self node runs in-process and needs none.
            for slot in slots:
                container=bound['nodes'][slot].get('container')
                require(isinstance(container,dict),'EXISTING_CONTAINER_REQUIRED')
                if not (isinstance(container,dict) and container.get('self') is True):
                    python=bound['nodes'][slot].get('python')
                    require(isinstance(python,str) and python.startswith('/'),'EXISTING_NODE_PYTHON_REQUIRED')
        devices=node_physical_devices(deployment) if deployment.get('instances') else {}
        if declared:
            require(existing,'DEVICE_ID_MAP_INVALID')
            valid=True
            for slot,mapping in declared.items():
                try:
                    validate_device_map(mapping,devices.get(slot,set()))
                except ValueError:
                    valid=False
            require(valid,'DEVICE_ID_MAP_INVALID')
            if existing and valid:
                # Only the local node can be probed offline; remote maps stay for
                # the create pre-check to confirm on their own host.
                for slot in declared:
                    if slot not in self_slots:
                        continue
                    observed=read_local_chip_logic_map()
                    if observed is None:
                        require(False,'DEVICE_ID_MAP_UNVERIFIABLE')
                    elif any(observed.get(int(key))!=value for key,value in declared[slot].items()):
                        require(False,'DEVICE_ID_MAP_MISMATCH')
        elif existing:
            # An undeclared map on a local self node can be probed offline. Stop
            # here instead of finishing publish/start only to fail at create.
            for slot in self_slots:
                observed=read_local_chip_logic_map()
                if observed is None:
                    break
                if any(observed.get(device)!=device for device in devices.get(slot,set())):
                    require(False,'DEVICE_ID_MAP_REQUIRED')
                    break
        evaluation=plan.get('evaluation')
        if evaluation:
            tester=bound['testers'][evaluation['test_binding']]
            require(machine(tester) is not None,'TESTER_MACHINE_ID_REQUIRED')
            container=tester.get('container')
            if existing:
                # A container tester must be the container of the node on the
                # same host; there is exactly one container per host, so this
                # also pins the tester to that host's node container.
                matches=[slot for slot in slots
                         if next(g['host'] for g in groups.values() if g['node']==slot)==tester['host']]
                require(len(matches)==1,'TESTER_CONTAINER_MISMATCH')
                node=bound['nodes'][matches[0]] if len(matches)==1 else {}
                require(isinstance(container,dict) and isinstance(node.get('container'),dict)
                        and container.get('id')==node['container'].get('id')
                        and ('image' not in container
                             or container.get('image')==node['container'].get('image')),
                        'TESTER_CONTAINER_MISMATCH')
            elif isinstance(container,dict):
                require(False,'TESTER_CONTAINER_FORBIDDEN')
    else:
        require(False,'INTENT_UNSUPPORTED')
    result=envelope(task,0,'ERROR' if blockers else 'EVENTS')
    result.update(readiness=dict(status='BLOCKED' if blockers else 'READY_FOR_HOST_PREFLIGHT',
        blockers=blockers,publication_targets=len(targets),local_payloads_verified=True,
        remote_checks='NOT_RUN',performance_acceptance='NOT_RUN'),started=False)
    if blockers:
        result['error']=dict(code='INVALID_INPUT',message='Readiness blockers; see readiness.blockers',retryable=False)
    return result
