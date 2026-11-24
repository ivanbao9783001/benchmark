"""Production executor selection from immutable inputs and reviewed host identity."""
import os
from pathlib import Path
import re
from .compiler import verify_frozen
from .publication import verify_runtime
from .launcher import validate_identity
from .external_executor import ExternalExecutor
from .owned_executor import OwnedExecutor
from .docker_node import DockerNode
from .existing_container_node import ExistingContainerNode
from .self_container_node import SelfContainerNode
from .ssh_node import SSHNode
from .remote_guard import HostGuard,resolve_guard_root
from .protocol import ProtocolError
from .identity import resolve_identity


def local_machine_id():
    if os.name!='posix':
        raise ProtocolError('INVALID_INPUT: runtime requires Linux')
    return resolve_identity()['machine_id']


def build_executor(root,identity,*,machine_id_reader=local_machine_id,guard_root=None):
    validate_identity(identity)
    root=Path(root)
    if str(root)!=identity['node_root']:
        raise ProtocolError('INVALID_INPUT: runtime node root identity')
    verify_runtime(root/'runtime',identity['runtime_sha256'])
    values=verify_frozen(root/'inputs',identity['bundle_sha256'],identity['runtime_sha256'])
    plan,bindings=values['plan.json'],values['bindings.json']
    if plan['task_id']!=identity['task_id']:
        raise ProtocolError('INVALID_INPUT: runtime frozen task identity')
    machine=machine_id_reader()
    if not re.fullmatch('[a-f0-9]{32}',machine):
        raise ProtocolError('INVALID_INPUT: machine identity evidence')
    args=(root,root/'inputs',identity['bundle_sha256'],identity['runtime_sha256'])
    if plan['intent']=='external_eval':
        tester=bindings['testers'][plan['test_binding']]
        if tester.get('machine_id')!=machine:
            raise ProtocolError('INVALID_INPUT: tester machine identity not reviewed or differs')
        return ExternalExecutor(*args,verified_host=tester['host'])
    if plan['intent']!='deploy':
        raise ProtocolError('INVALID_INPUT: executor intent')
    compiled=values['compiled.json']
    existing_mode=(plan.get('deployment',{}).get('runtime',{})
                   .get('mode','owned_container')=='existing_container')
    slots={g['node'] for g in compiled['topology']['groups'].values()}
    nodes={}
    local=[]
    for slot in slots:
        binding=bindings['nodes'][slot]
        if not re.fullmatch('[a-f0-9]{32}',binding.get('machine_id','')):
            raise ProtocolError('INVALID_INPUT: node machine identity not reviewed')
        if binding['machine_id']==machine:
            local.append(slot)
            if any(Path(g['root'])!=root for g in compiled['topology']['groups'].values() if g['node']==slot):
                raise ProtocolError('INVALID_INPUT: local node root binding differs')
            guard=HostGuard(guard_root or resolve_guard_root(binding,str(root)),
                            plan['task_id'],identity['nonce'])
            container=binding.get('container')
            if existing_mode:
                if isinstance(container,dict) and container.get('self') is True:
                    nodes[slot]=SelfContainerNode(guard,container)
                else:
                    nodes[slot]=ExistingContainerNode(guard,container)
            else:
                nodes[slot]=DockerNode(guard)
        else:
            nodes[slot]=SSHNode(plan['task_id'],slot,compiled,identity['bundle_sha256'],
                               identity['runtime_sha256'],identity['nonce'],coordinator_root=root)
    if len(local)!=1:
        raise ProtocolError('INVALID_INPUT: runtime must match one reviewed deployment node')
    self_slots=[slot for slot in slots
                if isinstance(bindings['nodes'][slot].get('container'),dict) and
                bindings['nodes'][slot]['container'].get('self') is True]
    if self_slots and (self_slots!=local or not existing_mode):
        raise ProtocolError('INVALID_INPUT: self container node must be the local node')
    tester_host=None
    options={}
    if 'evaluation' in plan:
        tester=bindings['testers'][plan['evaluation']['test_binding']]
        if not re.fullmatch('[a-f0-9]{32}',tester.get('machine_id','')):
            raise ProtocolError('INVALID_INPUT: tester machine identity not reviewed')
        if tester['machine_id']!=machine:
            from .remote_evaluation import RemoteEvaluation
            options['evaluator']=RemoteEvaluation(plan['task_id'],tester,identity['bundle_sha256'],
                                                 identity['runtime_sha256'],identity['nonce'],coordinator_root=root)
        tester_host=tester['host']
    return OwnedExecutor(*args,nodes,verified_tester_host=tester_host,**options)
