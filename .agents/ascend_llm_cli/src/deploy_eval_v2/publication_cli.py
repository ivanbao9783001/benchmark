"""JSON-file CLI contracts for runtime packaging and task publication."""
from pathlib import Path,PurePath,PurePosixPath
import re
import tempfile
from .compiler import verify_frozen
from .input_publication import build_input_archive
from .protocol import ProtocolError,envelope
from .publication import build_archive,install_archive
from .remote_guard import resolve_guard_root
from .ssh_publication import probe_guard_root,publish_task
from .ssh_transport import exchange,host_argv


def task_binding(config,task,*,delivery=False):
    if not isinstance(task,str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}',task):
        raise ProtocolError('INVALID_INPUT: task identity')
    if (not isinstance(config,dict) or not isinstance(config.get('tasks'),dict)
            or task not in config['tasks'] or not isinstance(config['tasks'][task],dict)):
        raise ProtocolError('INVALID_INPUT: task binding')
    binding=config['tasks'][task]
    root,allowed=PurePosixPath(binding['root']),PurePosixPath(binding['allowed_root'])
    if (not allowed.is_absolute() or '..' in allowed.parts or not root.is_absolute() or
            '..' in root.parts or root==allowed or not root.is_relative_to(allowed) or
            any(c in str(root) for c in '\x00\r\n\\')):
        raise ProtocolError('INVALID_INPUT: task root')
    if delivery:
        task_delivery_root(binding,task,root)
    return binding


def task_delivery_root(binding,task,node_root):
    """Validate the control-side delivery root; it never equals or contains node_root."""
    value=binding.get('task_root')
    if not isinstance(value,str) or not 1<=len(value)<=4096 or '\x00' in value or '\n' in value or '\r' in value:
        raise ProtocolError('INVALID_INPUT: task delivery root')
    path=PurePosixPath(value) if value.startswith('/') else PurePath(value)
    if (not path.is_absolute() or '..' in path.parts or path.name!=task
            or path==node_root or path.is_relative_to(node_root) or node_root.is_relative_to(path)):
        raise ProtocolError('INVALID_INPUT: task delivery root')
    return path


def package_runtime(request):
    if not isinstance(request,dict) or set(request)!={'destination'} or not isinstance(request['destination'],str) or not 1<=len(request['destination'])<=512:
        raise ProtocolError('INVALID_INPUT: package runtime request')
    result=build_archive(Path(__file__).parent,request['destination'])
    response=envelope(None,0)
    response['runtime_package']=result
    return response


def publication_targets(values,binding):
    targets=[('runtime',binding)]
    if values['plan.json']['intent']!='deploy':
        return targets
    groups=values['compiled.json']['topology']['groups']
    selected={}
    for group in groups.values():
        slot=group['node']
        if slot in selected:
            if (selected[slot]['host'],selected[slot]['root'])!=(group['host'],group['root']):
                raise ProtocolError('INVALID_INPUT: inconsistent node publication root')
            continue
        selected[slot]=group
        if (group['host'],group['root'])==(binding['host'],binding['root']):
            continue
        target=binding.get('nodes',{}).get(slot)
        if not isinstance(target,dict) or (target.get('host'),target.get('root'))!=(group['host'],group['root']):
            raise ProtocolError('INVALID_INPUT: node publication target differs from frozen binding')
        task_binding({'tasks':{'target':target}},'target')
        targets.append((slot,target))
    evaluation=values['plan.json'].get('evaluation')
    if evaluation:
        tester=values['bindings.json']['testers'][evaluation['test_binding']]
        test_root=str(PurePosixPath(tester['work_root'])/values['plan.json']['task_id'])
        if (tester['host'],test_root) not in {(item['host'],item['root']) for _,item in targets}:
            target=binding.get('tester')
            if not isinstance(target,dict) or (target.get('host'),target.get('root'))!=(tester['host'],test_root):
                raise ProtocolError('INVALID_INPUT: tester publication target differs from frozen binding')
            task_binding({'tasks':{'tester':target}},'tester')
            targets.append(('tester',target))
    return targets


def guard_targets(values):
    """Guard root per publication (host, root); requires a frozen deployment."""
    if values['plan.json']['intent']!='deploy':
        return {}
    groups=values['compiled.json']['topology']['groups']
    nodes=values['bindings.json']['nodes']
    return {(group['host'],group['root']):resolve_guard_root(nodes[group['node']],group['root'])
            for group in groups.values()}


def publish(request,config,*,publisher=publish_task,prober=probe_guard_root):
    required={'task_ref','runtime_archive','input_directory','runtime_sha256','bundle_sha256'}
    if not isinstance(request,dict) or set(request)!=required or any(not isinstance(v,str) or not 1<=len(v)<=512 for v in request.values()):
        raise ProtocolError('INVALID_INPUT: publication request')
    for key in ('runtime_sha256','bundle_sha256'):
        if not re.fullmatch('[a-f0-9]{64}',request[key]):
            raise ProtocolError('INVALID_INPUT: publication digest')
    task=request['task_ref']
    binding=task_binding(config,task,delivery=True)
    values=verify_frozen(request['input_directory'],request['bundle_sha256'],request['runtime_sha256'])
    if values['plan.json'].get('task_id')!=task:
        raise ProtocolError('INVALID_INPUT: publication task mismatch')
    if values['plan.json'].get('intent') not in ('deploy','external_eval'):
        raise ProtocolError('INVALID_INPUT: unsupported production plan')
    targets=publication_targets(values,binding)
    guards=guard_targets(values)
    # Inspect both local payloads before any remote side effect or connection.
    with tempfile.TemporaryDirectory(prefix='deploy-eval-publish-') as temporary:
        temporary=Path(temporary)
        install_archive(request['runtime_archive'],temporary/'runtime',request['runtime_sha256'])
        inputs=temporary/'inputs.zip'
        build_input_archive(request['input_directory'],inputs,request['bundle_sha256'],request['runtime_sha256'])
        completed=0
        for name,target in targets:
            guard=guards.get((target['host'],target['root']))
            if guard is not None:
                try:
                    prober(target['host'],target.get('python','/usr/bin/python3'),guard,task,
                           link='local' if target.get('self') is True else 'ssh')
                except (ProtocolError,OSError) as error:
                    response=envelope(task,0,'ERROR')
                    response.update(publication=dict(completed_targets=completed,total_targets=len(targets),
                                                     failed_target=name),started=False)
                    # Nothing was uploaded, so the caller may fix bindings and retry.
                    response['error']=dict(code='GUARD_ROOT_UNAVAILABLE',message=str(error)[:512],retryable=True)
                    return response
            try:
                publisher(target['host'],target.get('python','/usr/bin/python3'),target['root'],
                          request['runtime_archive'],inputs,task,request['runtime_sha256'],request['bundle_sha256'],
                          link='local' if target.get('self') is True else 'ssh')
                completed+=1
            except (ProtocolError,OSError):
                response=envelope(task,0,'ERROR')
                response.update(publication=dict(completed_targets=completed,total_targets=len(targets),failed_target=name),started=False)
                response['error']=dict(code='UNKNOWN_OUTCOME',message='Publication incomplete; verify existing payloads before retry',retryable=False)
                return response
    response=envelope(task,0)
    response.update(publication=dict(completed_targets=completed,total_targets=len(targets),
                                    runtime_sha256=request['runtime_sha256'],bundle_sha256=request['bundle_sha256']),started=False)
    return response


def start_task(request,config,*,transport=exchange):
    fields={'task_ref','runtime_sha256','bundle_sha256','nonce'}
    if not isinstance(request,dict) or set(request)!=fields:
        raise ProtocolError('INVALID_INPUT: start-task request')
    for key in fields-{'task_ref'}:
        if not isinstance(request[key],str) or not re.fullmatch('[a-f0-9]{64}',request[key]):
            raise ProtocolError('INVALID_INPUT: startup digest')
    binding=task_binding(config,request['task_ref'],delivery=True)
    root=binding['root']
    python=binding.get('python','/usr/bin/python3')
    if not isinstance(python,str) or not python.startswith('/'):
        raise ProtocolError('INVALID_INPUT: absolute startup Python required')
    bootstrap='import sys; sys.path.insert(0, '+repr(root+'/runtime')+'); from deploy_eval_v2.startup import main; raise SystemExit(main())'
    remote=dict(protocol_version='1.0',task_id=request['task_ref'],python=python,
                **{k:request[k] for k in fields-{'task_ref'}})
    link='local' if binding.get('self') is True else 'ssh'
    return transport(host_argv(binding['host'],[python,'-B','-c',bootstrap,'--root',root],link),remote,timeout=90)
