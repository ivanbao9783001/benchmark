import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from deploy_eval_v2.deploy_scripts import compile_service_scripts
from deploy_eval_v2.deployment_compiler import compile_bound_deployment
from deploy_eval_v2.compiler import digest
from deploy_eval_v2.existing_container_node import STOP_SCRIPT, ExistingContainerNode
from deploy_eval_v2.json_io import load_json
from deploy_eval_v2.node_worker import dispatch
from deploy_eval_v2.protocol import ProtocolError
from deploy_eval_v2.remote_guard import HostGuard
from deploy_eval_v2.review_summary import summarize
from test_deployment_model import deploy_plan

CID = 'a'*64
IMAGE = 'sha256:'+'b'*64


def chip_map(*rows):
    """Render `npu-smi info -m` rows as the container reports them."""
    lines = [b'\tNPU ID  Chip ID  Chip Logic ID  Chip Name']
    lines += [('\t'+str(npu)+'  0  '+str(logic)+'  Ascend 910B4').encode()
              for npu, logic in rows]
    return b'\n'.join(lines)+b'\n'


def owned_bindings():
    value = load_json(Path(__file__).parent/'fixtures/deploy-bindings.json')
    value.update(schema_version='1.0', task_id='t')
    return value


def existing_bindings():
    value = owned_bindings()
    node = value['nodes']['node_a']
    node['container'] = dict(id=CID, image=IMAGE)
    node['container_work_root'] = '/workspace/tasks'
    return value


def existing_plan():
    plan = deploy_plan()
    runtime = plan['deployment']['runtime']
    runtime.clear()
    runtime['mode'] = 'existing_container'
    return plan


class ExistingContainerCompileTests(unittest.TestCase):
    def test_borrowed_group_uses_container_root_without_owned_fields(self):
        plan = existing_plan()
        original = json.dumps(plan, sort_keys=True)
        result = compile_bound_deployment(plan, existing_bindings())
        self.assertEqual(json.dumps(plan, sort_keys=True), original)
        group = result['groups']['node_a/model']
        self.assertEqual(group['root'], '/path/to/task-workspace/t')
        self.assertEqual(group['container_root'], '/workspace/tasks/t')
        self.assertEqual(group['mounts'], [])
        self.assertNotIn('container', group)
        self.assertNotIn('image', group)
        # Runtime paths expand inside the borrowed container.
        self.assertTrue(any('/workspace/tasks/t/logs/prefill/logs/profiler' in value
                            for value in group['commands'][0]['argv']))

    def test_self_flag_is_projected_into_the_compiled_group(self):
        bound = existing_bindings()
        self.assertNotIn('self', compile_bound_deployment(existing_plan(), bound)['groups']['node_a/model'])
        bound['nodes']['node_a']['container']['self'] = True
        group = compile_bound_deployment(existing_plan(), bound)['groups']['node_a/model']
        self.assertIs(group['self'], True)

    def test_self_flag_requires_one_placed_borrowed_node(self):
        bound = existing_bindings()
        bound['nodes']['node_a']['container']['self'] = True
        bound['nodes']['node_b'] = json.loads(json.dumps(bound['nodes']['node_a']))
        with self.assertRaisesRegex(ProtocolError, 'self container node'):
            compile_bound_deployment(existing_plan(), bound)

    def test_scripts_separate_host_source_from_container_execution(self):
        result = compile_bound_deployment(existing_plan(), existing_bindings())
        script = next(item for item in compile_service_scripts(result)['node_a/model']
                      if item['instance'] == 'prefill' and item['purpose'] == 'service')
        self.assertEqual(script['path'], '/path/to/task-workspace/t/scripts/prefill.service.sh')
        self.assertEqual(script['container_path'],
                         '/workspace/tasks/t/scripts/prefill.service.sh')
        self.assertEqual(script['log']['path'], '/workspace/tasks/t/logs/prefill.service.log')
        self.assertEqual(script['transport_log'],
                         '/path/to/task-workspace/t/logs/prefill.service.transport.log')

    def test_owned_compilation_is_unchanged_by_the_new_contract(self):
        plan, bound = deploy_plan(), owned_bindings()
        baseline = compile_bound_deployment(plan, bound)
        explicit = json.loads(json.dumps(plan))
        explicit['deployment']['runtime']['mode'] = 'owned_container'
        self.assertEqual(json.dumps(compile_bound_deployment(explicit, bound), sort_keys=True),
                         json.dumps(baseline, sort_keys=True))
        group = baseline['groups']['node_a/model']
        self.assertNotIn('container_root', group)
        self.assertIn('container', group)
        self.assertIn('image', group)
        for script in compile_service_scripts(baseline)['node_a/model']:
            self.assertNotIn('container_path', script)
            self.assertNotIn('transport_log', script)
        prefill = next(script for script in compile_service_scripts(baseline)['node_a/model']
                       if script['instance'] == 'prefill' and script['purpose'] == 'service')
        self.assertEqual(prefill['log']['path'],
                         '/path/to/task-workspace/t/logs/prefill.service.log')

    def test_borrowed_binding_is_required_only_in_existing_mode(self):
        plan, bound = existing_plan(), existing_bindings()
        for kind in ('container', 'work_root'):
            with self.subTest(kind=kind):
                broken = json.loads(json.dumps(bound))
                del broken['nodes']['node_a']['container' if kind == 'container' else 'container_work_root']
                with self.assertRaisesRegex(ProtocolError, 'existing container binding'):
                    compile_bound_deployment(plan, broken)
        with self.assertRaisesRegex(ProtocolError, 'borrowed container binding'):
            compile_bound_deployment(deploy_plan(), existing_bindings())

    def test_existing_mode_rejects_owned_runtime_configuration(self):
        for field in ('image', 'container'):
            with self.subTest(field=field):
                plan = existing_plan()
                plan['deployment']['runtime'][field] = ('x' if field == 'image'
                                                        else dict(privileged=False))
                with self.assertRaises(ProtocolError):
                    compile_bound_deployment(plan, existing_bindings())

    def test_review_reports_borrowed_authority_without_owned_container_claims(self):
        summary = summarize(existing_plan(), existing_bindings())
        self.assertEqual(summary['authority']['deployment'], 'existing_container')
        self.assertEqual(summary['authority']['cleanup_scope'], 'task_processes')
        self.assertEqual(summary['effective_container'],
                         {'mode': 'existing_container', 'create': False, 'remove': False})
        self.assertEqual(summary['container_defaults'], [])
        baseline = summarize(deploy_plan(), owned_bindings())
        self.assertEqual(baseline['authority']['deployment'], 'owned')
        self.assertNotIn('cleanup_scope', baseline['authority'])
        self.assertNotIn('mode', baseline['effective_container'])
        self.assertTrue(baseline['container_defaults'])


class ExistingContainerNodeTests(unittest.TestCase):
    def node(self, folder, runner):
        guard = HostGuard(Path(folder)/'guard', 't', 'b'*64)
        return ExistingContainerNode(guard, dict(id=CID, image=IMAGE), runner), guard

    def test_create_inspects_and_never_creates_or_starts_a_container(self):
        with tempfile.TemporaryDirectory() as folder:
            inspect = json.dumps(dict(Id=CID, Image=IMAGE, Running=True)).encode()
            runner = Mock(side_effect=[(1, b'', b''), (0, b'', b''), (0, inspect, b''), (0, b'', b'')])
            node, _ = self.node(folder, runner)
            group = dict(npu_ids=[0], ports=[], root=str(Path(folder)/'task'),
                         container_root='/workspace/tasks/t')
            result = node.create('create1', group, time.monotonic()+30, threading.Event())
            self.assertEqual(result['container_id'], CID)
            calls = [call.args[0] for call in runner.call_args_list]
            self.assertEqual(calls[0][0], 'fuser')
            self.assertEqual(calls[1], ['docker', 'exec', CID, 'npu-smi', 'info', '-m'])
            self.assertIn('inspect', calls[2])
            self.assertEqual(calls[3][:4], ['docker', 'exec', CID, 'mkdir'])
            for argv in calls:
                self.assertNotIn('create', argv)
                self.assertNotIn('start', argv)
                self.assertNotIn('rm', argv)

    def test_declared_device_map_is_verified_against_the_container(self):
        with tempfile.TemporaryDirectory() as folder:
            inspect = json.dumps(dict(Id=CID, Image=IMAGE, Running=True)).encode()
            runner = Mock(side_effect=[(1, b'', b''), (1, b'', b''),
                                       (0, chip_map((5, 0), (6, 1)), b''),
                                       (0, inspect, b''), (0, b'', b'')])
            node, guard = self.node(folder, runner)
            group = dict(npu_ids=[5, 6], ports=[], root=str(Path(folder)/'task'),
                         container_root='/workspace/tasks/t',
                         device_id_map={'5': 0, '6': 1})
            node.create('create1', group, time.monotonic()+30, threading.Event())
            evidence = load_json(guard.root/'probes'/(digest(['create1', 'device-map'])+'.json'))
            self.assertEqual(evidence['status'], 'VERIFIED')
            self.assertEqual(evidence['observed'], {'5': 0, '6': 1})

    def test_declared_device_map_mismatch_blocks_the_create(self):
        with tempfile.TemporaryDirectory() as folder:
            runner = Mock(side_effect=[(1, b'', b''), (1, b'', b''),
                                       (0, chip_map((5, 0), (6, 2)), b'')])
            node, _ = self.node(folder, runner)
            group = dict(npu_ids=[5, 6], ports=[], root=str(Path(folder)/'task'),
                         container_root='/workspace/tasks/t',
                         device_id_map={'5': 0, '6': 1})
            with self.assertRaisesRegex(ProtocolError, 'RECOVERY_REQUIRED'):
                node.create('create1', group, time.monotonic()+30, threading.Event())

    def test_undeclared_remapping_requires_an_explicit_map(self):
        with tempfile.TemporaryDirectory() as folder:
            runner = Mock(side_effect=[(1, b'', b''), (1, b'', b''),
                                       (0, chip_map((5, 0), (6, 1)), b'')])
            node, _ = self.node(folder, runner)
            group = dict(npu_ids=[5, 6], ports=[], root=str(Path(folder)/'task'),
                         container_root='/workspace/tasks/t')
            with self.assertRaisesRegex(ProtocolError, 'declare device_id_map'):
                node.create('create1', group, time.monotonic()+30, threading.Event())

    def test_identity_numbering_without_a_map_proceeds(self):
        with tempfile.TemporaryDirectory() as folder:
            inspect = json.dumps(dict(Id=CID, Image=IMAGE, Running=True)).encode()
            runner = Mock(side_effect=[(1, b'', b''), (1, b'', b''),
                                       (0, chip_map((5, 5), (6, 6)), b''),
                                       (0, inspect, b''), (0, b'', b'')])
            node, _ = self.node(folder, runner)
            group = dict(npu_ids=[5, 6], ports=[], root=str(Path(folder)/'task'),
                         container_root='/workspace/tasks/t')
            self.assertEqual(node.create('create1', group, time.monotonic()+30,
                                         threading.Event())['container_id'], CID)

    def test_unavailable_chip_map_without_a_declaration_keeps_legacy_behaviour(self):
        with tempfile.TemporaryDirectory() as folder:
            inspect = json.dumps(dict(Id=CID, Image=IMAGE, Running=True)).encode()
            runner = Mock(side_effect=[(1, b'', b''), (1, b'', b'npu-smi'),
                                       (0, inspect, b''), (0, b'', b'')])
            node, guard = self.node(folder, runner)
            group = dict(npu_ids=[5], ports=[], root=str(Path(folder)/'task'),
                         container_root='/workspace/tasks/t')
            node.create('create1', group, time.monotonic()+30, threading.Event())
            evidence = load_json(guard.root/'probes'/(digest(['create1', 'device-map'])+'.json'))
            self.assertEqual(evidence['status'], 'INCONCLUSIVE')

    def test_unavailable_chip_map_with_a_declaration_requires_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            runner = Mock(side_effect=[(1, b'', b''), (1, b'', b''), (1, b'', b'npu-smi')])
            node, _ = self.node(folder, runner)
            group = dict(npu_ids=[5, 6], ports=[], root=str(Path(folder)/'task'),
                         container_root='/workspace/tasks/t',
                         device_id_map={'5': 0, '6': 1})
            with self.assertRaisesRegex(ProtocolError, 'RECOVERY_REQUIRED'):
                node.create('create1', group, time.monotonic()+30, threading.Event())

    def test_start_reverifies_and_service_enters_the_borrowed_container(self):
        with tempfile.TemporaryDirectory() as folder:
            inspect = json.dumps(dict(Id=CID, Image=IMAGE, Running=True)).encode()
            runner = Mock(side_effect=[(0, inspect, b''), (0, inspect, b''),
                                       (0, b'', b''), (0, b'', b'')])
            node, _ = self.node(folder, runner)
            deadline, cancelled = time.monotonic()+30, threading.Event()
            self.assertTrue(node.start_container('start1', CID, deadline, cancelled)['started'])
            script = Path(folder)/'prefill.service.sh'
            script.write_text('#!/bin/bash\n')
            entry = dict(instance='prefill', purpose='service', path=str(script),
                         sha256=hashlib.sha256(script.read_bytes()).hexdigest(),
                         container_path='/workspace/tasks/t/scripts/prefill.service.sh',
                         log=dict(host='host', path='/workspace/tasks/t/logs/prefill.service.log'))
            node.start_service('service1', CID, entry, deadline, cancelled)
            calls = [call.args[0] for call in runner.call_args_list]
            self.assertEqual(calls[2], ['docker', 'cp', str(script),
                                        CID+':/workspace/tasks/t/scripts/prefill.service.sh'])
            self.assertEqual(calls[3], ['docker', 'exec', '-d', '-e', 'BENCH_TASK_ID=t',
                                        CID, 'setsid', 'bash',
                                        '/workspace/tasks/t/scripts/prefill.service.sh'])

    def test_ports_are_probed_inside_the_container_namespace(self):
        with tempfile.TemporaryDirectory() as folder:
            inspect = json.dumps(dict(Id=CID, Image=IMAGE, Running=True)).encode()
            runner = Mock(side_effect=[(1, b'', b''), (0, b'', b''), (0, inspect, b''),
                                       (0, b'BUSY 18056\n', b'')])
            node, _ = self.node(folder, runner)
            group = dict(npu_ids=[0], ports=[18056], root=str(Path(folder)/'task'),
                         container_root='/workspace/tasks/t')
            with self.assertRaisesRegex(ProtocolError, 'RESOURCE_BUSY'):
                node.create('create1', group, time.monotonic()+30, threading.Event())
            probe = runner.call_args_list[3].args[0]
            self.assertEqual(probe[:5], ['docker', 'exec', CID, 'python3', '-c'])
            self.assertEqual(probe[-1], '18056')

    def test_identity_or_image_drift_blocks_every_effect(self):
        with tempfile.TemporaryDirectory() as folder:
            drift = json.dumps(dict(Id=CID, Image='sha256:'+'c'*64, Running=True)).encode()
            runner = Mock(side_effect=[(1, b'', b''), (0, b'', b''), (0, drift, b'')])
            node, _ = self.node(folder, runner)
            group = dict(npu_ids=[0], ports=[], root=str(Path(folder)/'task'),
                         container_root='/workspace/tasks/t')
            with self.assertRaisesRegex(ProtocolError, 'identity or running state'):
                node.create('create1', group, time.monotonic()+30, threading.Event())
            self.assertEqual(runner.call_count, 3)

    def test_release_stops_marked_groups_and_keeps_the_container(self):
        with tempfile.TemporaryDirectory() as folder:
            runner = Mock(side_effect=[(0, b'ABSENT\n', b''), (1, b'', b'')])
            node, guard = self.node(folder, runner)
            guard.claim(['npu:0'])
            evidence = Path(folder)/'release.json'
            result = node.release_existing('release1', [CID], ['npu:0'], evidence)
            self.assertEqual(result['cleanup_status'], 'VERIFIED')
            self.assertTrue(result['claims_released'])
            stop = runner.call_args_list[0].args[0]
            self.assertEqual(stop[:5], ['docker', 'exec', CID, 'python3', '-c'])
            self.assertEqual(stop[6:8], ['t', '10.0'])
            for call in runner.call_args_list:
                self.assertNotIn('rm', call.args[0])
            self.assertTrue(evidence.exists())
            # Claims are free again only after verified absence.
            HostGuard(Path(folder)/'guard', 'next', 'd'*64).claim(['npu:0'])

    def test_unverified_release_retains_claims(self):
        with tempfile.TemporaryDirectory() as folder:
            runner = Mock(return_value=(0, b'PRESENT\n', b''))
            node, guard = self.node(folder, runner)
            guard.claim(['npu:0'])
            result = node.release_existing('release1', [CID], ['npu:0'], Path(folder)/'release.json')
            self.assertEqual(result['cleanup_status'], 'UNVERIFIED')
            self.assertFalse(result['claims_released'])
            with self.assertRaisesRegex(ProtocolError, 'RESOURCE_BUSY'):
                HostGuard(Path(folder)/'guard', 'next', 'd'*64).claim(['npu:0'])

    def test_invalid_identity_is_rejected_before_any_effect(self):
        with tempfile.TemporaryDirectory() as folder:
            guard = HostGuard(Path(folder)/'guard', 't', 'b'*64)
            for container in (None, dict(id='short', image=IMAGE), dict(id=CID, image='tag')):
                with self.subTest(container=container), self.assertRaises(ProtocolError):
                    ExistingContainerNode(guard, container, Mock())


class ExistingContainerWorkerTests(unittest.TestCase):
    def test_worker_routes_release_to_the_borrowed_container_adapter(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'task'
            group = dict(node='n', root=str(root), host='host', npu_ids=[0], ports=[],
                         container_root='/workspace/tasks/t')
            values = dict(
                plan=dict(task_id='t', intent='deploy',
                          deployment=dict(runtime=dict(mode='existing_container'))),
                bindings={'nodes': {'n': dict(container=dict(id=CID, image=IMAGE))}},
                compiled=dict(topology=dict(groups={'g': group})))
            request = dict(protocol_version='1.0', task_id='t', node='n', action='release',
                           command_id='release1', runtime_sha256='a'*64, bundle_sha256='b'*64,
                           token='c'*64, timeout_seconds=10, containers=[CID], keep_claims=False)
            node = Mock()
            node.release_existing.return_value = dict(cleanup_status='VERIFIED', claims_released=True)
            with patch('deploy_eval_v2.node_worker.verify_runtime'), \
                 patch('deploy_eval_v2.node_worker.verify_frozen',
                       return_value={'plan.json': values['plan'],
                                     'bindings.json': values['bindings'],
                                     'compiled.json': values['compiled']}):
                result = dispatch(root, request, guard_root=Path(folder)/'guard',
                                  node_factory=Mock(return_value=node))
            node.release_existing.assert_called_once()
            args = node.release_existing.call_args
            self.assertEqual(args.args[0], 'release1')
            self.assertEqual(args.args[1], [CID])
            self.assertEqual(args.args[2], ['npu:0'])
            self.assertEqual(args.kwargs, dict(keep_claims=False, timeout=10))
            self.assertEqual(result['result']['cleanup_status'], 'VERIFIED')


class StopScriptBoundaryTests(unittest.TestCase):
    def test_marker_is_matched_on_the_group_leader_only(self):
        # vLLM workers drop BENCH_TASK_ID from their environment, so membership
        # must not be required from every process in the group.
        self.assertIn('if pid!=group: continue', STOP_SCRIPT)
        self.assertNotIn('marked-unmarked', STOP_SCRIPT)

    def test_targets_are_recorded_before_signalling(self):
        # After the leader exits, its group must still be tracked; otherwise an
        # orphaned worker group would be reported absent.
        self.assertIn('owners,alive=scan()', STOP_SCRIPT)
        self.assertIn('targets={group for group,marked in owners.items()', STOP_SCRIPT)
        self.assertIn('return {group for group in targets if group in current}', STOP_SCRIPT)
        self.assertIn('signal_groups(signal.SIGKILL,survivors())', STOP_SCRIPT)

