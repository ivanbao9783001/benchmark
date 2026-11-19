import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from deploy_eval_v2.container_tester import ContainerTester, worker
from deploy_eval_v2.protocol import ProtocolError


class ContainerTesterTest(unittest.TestCase):
    def test_identity_mismatch_never_writes_container(self):
        tester = dict(python='/bin/python3', work_root='/work',
                      container=dict(id='a'*64, image='sha256:'+'b'*64))
        with patch.object(ContainerTester, 'command', return_value=json.dumps([
                dict(Id='c'*64, Image=tester['container']['image'], State={'Running': True})]).encode()) as command:
            with self.assertRaises(ProtocolError):
                ContainerTester(tester, '/work/task/results/op/benchmark', time.monotonic()+30)
            self.assertEqual(command.call_count, 1)

    def test_container_work_root_maps_host_paths(self):
        instance = object.__new__(ContainerTester)
        instance.host_root = Path('/work')
        instance.container_root = Path('/workspace')
        instance.folder = Path('/work/task/results/op/benchmark')
        instance.inside_folder = instance.inside(instance.folder)
        self.assertEqual(instance.inside(Path('/work/task/x')), '/workspace/task/x')
        self.assertEqual(instance.argument('/work/task/results/op/benchmark/config.py'),
                         '/workspace/task/results/op/benchmark/config.py')
        instance.source_runtime = Path('/srv/runtime')
        instance.runtime = '/container-tools/runtime'
        self.assertEqual(instance.argument('/srv/runtime/x.py'), '/container-tools/runtime/x.py')
        with self.assertRaises(ProtocolError):
            instance.inside(Path('/etc/passwd'))
        payload = dict(config='/work/task/results/op/benchmark/aisbench-config.py', folder='/work/task',
                       tester=dict(work_root='/work', python='/workspace/venvs/aisbench/bin/python'),
                       endpoint='http://node:8080/health')
        translated = instance.translate(payload)
        self.assertEqual(translated['config'], '/workspace/task/results/op/benchmark/aisbench-config.py')
        self.assertEqual(translated['folder'], '/workspace/task')
        self.assertEqual(translated['tester']['work_root'], '/workspace')
        self.assertEqual(translated['tester']['python'], '/workspace/venvs/aisbench/bin/python')
        self.assertEqual(translated['endpoint'], 'http://node:8080/health')

    def test_worker_uses_explicit_environment_and_records_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = root/'request.json'
            done = root/'done.json'
            request.write_text(json.dumps(dict(argv=['tool'], log=str(root/'aisbench.log'),
                timeout=10, environment={'TEST_BINDING': 'bound'}, done=str(done),
                cancel=str(root/'cancel'), root=str(root))))
            with patch('deploy_eval_v2.container_tester.run_logged', return_value=0) as run:
                worker(request)
            self.assertEqual(run.call_args.kwargs['env']['TEST_BINDING'], 'bound')
            self.assertEqual(json.loads(done.read_text())['cleanup_status'], 'VERIFIED')

    def test_worker_unknown_failure_does_not_claim_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = root/'request.json'
            done = root/'done.json'
            request.write_text(json.dumps(dict(argv=['tool'], log=str(root/'aisbench.log'),
                timeout=10, environment={}, done=str(done), cancel=str(root/'cancel'), root=str(root))))
            with patch('deploy_eval_v2.container_tester.run_logged', side_effect=OSError('unknown')):
                worker(request)
            self.assertEqual(json.loads(done.read_text())['cleanup_status'], 'UNVERIFIED')
