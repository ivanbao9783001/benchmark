import hashlib
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from deploy_eval_v2.compiler import digest
from deploy_eval_v2.json_io import load_json
from deploy_eval_v2.protocol import ProtocolError
from deploy_eval_v2.remote_guard import HostGuard
from deploy_eval_v2.self_container_node import SelfContainerNode
from deploy_eval_v2.service_contract import validate_service

CID = 'c'*64
IMAGE = 'sha256:'+'d'*64


class SelfContainerNodeTests(unittest.TestCase):
    def node(self, folder, runner):
        guard = HostGuard(Path(folder)/'guard', 'task', 'a'*64)
        return SelfContainerNode(guard, dict(id=CID, image=IMAGE), runner), guard

    def test_create_runs_every_probe_locally(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder)/'dockerenv'
            marker.write_text('')
            free = (0, b'\tNo process in device.\n', b'')
            runner = Mock(side_effect=[free, free, (0, b'FREE\n', b'')])
            node, guard = self.node(folder, runner)
            group = dict(npu_ids=[6, 7], ports=[8022],
                         root=str(Path(folder)/'task'),
                         container_root=str(Path(folder)/'container'))
            with patch('deploy_eval_v2.self_container_node.DOCKERENV', marker):
                result = node.create('create-1', group, time.monotonic()+60, threading.Event())
            self.assertEqual(result['container_id'], CID)
            self.assertEqual(set(result), {'container_id', 'image_id', 'name', 'resources'})
            evidence = guard.root/'probes'/(digest(['create-1', 'container'])+'.json')
            self.assertEqual(load_json(evidence)['image_verified'], False)
            calls = [item.args[0] for item in runner.call_args_list]
            self.assertEqual(calls[0], ['npu-smi', 'info', '-t', 'proc-mem', '-i', '6'])
            self.assertEqual(calls[2], [sys.executable, '-c', calls[2][2], '8022'])
            self.assertFalse(any('docker' in argv for argv in calls))
            self.assertTrue((Path(folder)/'container'/'scripts').is_dir())

    def test_create_result_satisfies_the_owned_service_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder)/'dockerenv'
            marker.write_text('')
            free = (0, b'\tNo process in device.\n', b'')
            runner = Mock(side_effect=[free, free, (0, b'FREE\n', b'')])
            node, _ = self.node(folder, runner)
            group = dict(npu_ids=[6, 7], ports=[8022],
                         root=str(Path(folder)/'task'),
                         container_root=str(Path(folder)/'container'))
            with patch('deploy_eval_v2.self_container_node.DOCKERENV', marker):
                created = node.create('create-1', group, time.monotonic()+60, threading.Event())
            service = dict(schema_version='1.0', task_id='task', service_id='svc-'+'a'*32,
                           ownership='owned', endpoint='http://127.0.0.1:8022', model='granite',
                           bundle_sha256='a'*64, runtime_sha256='b'*64,
                           health_time='2026-11-20T00:00:00Z',
                           health_evidence_ref='results/deploy/stage-result.json',
                           policy=dict(idle_timeout_seconds=60, on_idle='keep_service',
                                       on_failure='release_owned'),
                           deployment_summary=dict(container_groups=1),
                           containers={'node-a': created}, metrics=[], health_path='/health')
            validate_service(service)

    def test_inspection_fails_closed_outside_the_container(self):
        with tempfile.TemporaryDirectory() as folder:
            node, _ = self.node(folder, Mock())
            with patch('deploy_eval_v2.self_container_node.DOCKERENV', Path(folder)/'absent'):
                with self.assertRaisesRegex(ProtocolError, 'self container evidence'):
                    node.inspect_running(CID, time.monotonic()+30, threading.Event())
                with self.assertRaisesRegex(ProtocolError, 'self container evidence'):
                    node.start_container('start-1', CID, time.monotonic()+30, threading.Event())

    def test_service_starts_locally_with_the_execution_marker(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder)/'dockerenv'
            marker.write_text('')
            source = Path(folder)/'scripts'/'model.service.sh'
            source.parent.mkdir(parents=True)
            source.write_bytes(b'echo start\n')
            target = Path(folder)/'container'/'scripts'/'model.service.sh'
            script = dict(path=str(source), container_path=str(target), sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                          instance='node-a/model', log=dict(path=str(Path(folder)/'logs'/'model.log')))
            node, _ = self.node(folder, Mock())
            with patch('deploy_eval_v2.self_container_node.DOCKERENV', marker), \
                    patch('deploy_eval_v2.self_container_node.subprocess.Popen') as popen:
                result = node.start_service('service-1', CID, script, time.monotonic()+30, threading.Event())
            self.assertEqual(target.read_bytes(), b'echo start\n')
            self.assertEqual(popen.call_args.args[0], ['bash', str(target)])
            self.assertTrue(popen.call_args.kwargs['start_new_session'])
            self.assertEqual(popen.call_args.kwargs['env']['BENCH_TASK_ID'], 'task')
            self.assertEqual(result['status'], 'START_ACCEPTED')

    def test_busy_or_unreadable_device_blocks_create(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder)/'dockerenv'
            marker.write_text('')
            node, _ = self.node(folder, Mock(return_value=(0, b'process 123\n', b'')))
            group = dict(npu_ids=[6], ports=[], root=str(Path(folder)/'task'),
                         container_root=str(Path(folder)/'container'))
            with patch('deploy_eval_v2.self_container_node.DOCKERENV', marker):
                with self.assertRaisesRegex(ProtocolError, 'RESOURCE_BUSY'):
                    node.create('create-1', group, time.monotonic()+60, threading.Event())
            node, _ = self.node(folder, Mock(return_value=(1, b'', b'not supported')))
            with patch('deploy_eval_v2.self_container_node.DOCKERENV', marker):
                with self.assertRaisesRegex(ProtocolError, 'inconclusive'):
                    node.create('create-2', group, time.monotonic()+60, threading.Event())

    def test_release_absence_uses_the_driver_view(self):
        with tempfile.TemporaryDirectory() as folder:
            node, _ = self.node(folder, Mock(return_value=(0, b'No process in device.\n', b'')))
            results = node._verify_resources_absent(['npu:6'], time.monotonic()+30)
            self.assertEqual(results, [dict(resource='npu:6', status='ABSENT_VERIFIED')])
            node, _ = self.node(folder, Mock(return_value=(0, b'process 123\n', b'')))
            results = node._verify_resources_absent(['npu:6'], time.monotonic()+30)
            self.assertEqual(results, [dict(resource='npu:6', status='UNVERIFIED')])

    def test_port_probe_and_stop_use_local_processes(self):
        with tempfile.TemporaryDirectory() as folder:
            runner = Mock(side_effect=[(0, b'FREE\n', b''), (0, b'ABSENT\n', b'')])
            node, _ = self.node(folder, runner)
            deadline = time.monotonic()+60
            self.assertTrue(node._probe_ports(CID, [8022], deadline))
            self.assertEqual(node._stop_groups(CID, deadline), 'STOPPED_VERIFIED')
            self.assertEqual(runner.call_args_list[0].args[0][0], sys.executable)
            self.assertEqual(runner.call_args_list[0].args[0][3:], ['8022'])
            self.assertFalse(any('docker' in item.args[0] for item in runner.call_args_list))


if __name__ == '__main__':
    unittest.main()
