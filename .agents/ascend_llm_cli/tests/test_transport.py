import sys
import unittest
import threading
import time
from deploy_eval_v2.ssh_transport import exchange, host_argv, ssh_argv, container_argv
from deploy_eval_v2.protocol import ProtocolError


class TransportTests(unittest.TestCase):
    def run_child(self, program):
        return exchange([sys.executable, '-c', program], {'task_id': 't'}, timeout=3)

    def test_valid_and_stderr_flood(self):
        result = self.run_child("import sys; sys.stderr.write('x'*1000000); print('{\"protocol_version\":\"1.0\",\"task_id\":\"t\",\"status\":\"WAIT_TIMEOUT\"}')")
        self.assertEqual(result['status'], 'WAIT_TIMEOUT')

    def test_banner_and_oversize(self):
        for program in ("print('banner')", "print('x'*1000000)"):
            with self.assertRaisesRegex(ProtocolError, 'TRANSPORT_PROTOCOL_ERROR'):
                self.run_child(program)

    def test_unknown_outcome(self):
        with self.assertRaisesRegex(ProtocolError, 'UNKNOWN_OUTCOME'):
            exchange([sys.executable, '-c', 'raise SystemExit(255)'],
                     {'task_id': 't', 'kind': 'deploy'}, timeout=3)

    def test_read_only_child_failure_is_observer_unavailable(self):
        # A crashed observer says nothing about the task outcome; a mutating
        # command still reports that its side effect is unknown.
        argv = [sys.executable, '-c', 'raise SystemExit(255)']
        for kind in ('get-status', 'wait-event', 'read-result'):
            with self.assertRaisesRegex(ProtocolError, 'OBSERVER_UNAVAILABLE'):
                exchange(argv, {'task_id': 't', 'kind': kind}, timeout=3)
        with self.assertRaisesRegex(ProtocolError, 'UNKNOWN_OUTCOME'):
            exchange(argv, {'task_id': 't', 'kind': 'release'}, timeout=3)

    def test_observer_failure_carries_stderr_diagnostic(self):
        argv = [sys.executable, '-c', 'import observer_missing_dependency']
        with self.assertRaisesRegex(ProtocolError, 'observer_missing_dependency'):
            exchange(argv, {'task_id': 't', 'kind': 'get-status'}, timeout=3)
        # Mutating commands keep the terse, stable public message.
        with self.assertRaises(ProtocolError) as caught:
            exchange(argv, {'task_id': 't', 'kind': 'deploy'}, timeout=3)
        self.assertEqual(str(caught.exception), 'UNKNOWN_OUTCOME')

    def test_no_pty_and_host_validation(self):
        self.assertIn('-T', ssh_argv('user@host', ['python3', '/a b/client.py']))
        with self.assertRaises(ProtocolError):
            ssh_argv('-oProxyCommand=bad', [])

    def test_self_link_runs_the_same_argv_locally(self):
        remote=['python3','-B','-c','print(1)']
        self.assertEqual(host_argv('user@host',remote),ssh_argv('user@host',remote))
        self.assertEqual(host_argv('user@host',remote,'local'),remote)
        with self.assertRaises(ProtocolError):
            host_argv('user@host',remote,'telnet')

    def test_container_link_wraps_the_host_ssh_with_docker_exec(self):
        remote=['/opt/venv/bin/python','-B','-c','print(1)']
        cid='a'*64
        argv=container_argv('user@host',cid,remote)
        self.assertEqual(argv,ssh_argv('user@host',['docker','exec','-i',cid,*remote]))
        self.assertIn('docker exec -i '+cid,argv[-1])
        self.assertEqual(host_argv('user@host',remote,'container',cid),argv)
        for bad in ('short','A'*64,None):
            with self.assertRaisesRegex(ProtocolError,'container identity'):
                container_argv('user@host',bad,remote)

    def test_container_exec_failure_is_invalid_input_not_unknown_outcome(self):
        argv=[sys.executable,'-c','raise SystemExit(125)']
        with self.assertRaisesRegex(ProtocolError,'container exec unavailable'):
            exchange(argv,{'task_id':'t'},timeout=3,container=True)
        with self.assertRaisesRegex(ProtocolError,'UNKNOWN_OUTCOME'):
            exchange(argv,{'task_id':'t'},timeout=3)

    def test_duplicate_keys_and_nonfinite_json_are_rejected(self):
        for data in ('{"protocol_version":"1.0","task_id":"other","task_id":"t","status":"EVENTS"}',
                     '{"protocol_version":"1.0","task_id":"t","status":"EVENTS","value":NaN}'):
            with self.assertRaisesRegex(ProtocolError,'TRANSPORT_PROTOCOL_ERROR'):
                self.run_child('print('+repr(data)+')')

    def test_cancel_waiting_transport_has_bounded_duration(self):
        flag=threading.Event()
        timer=threading.Timer(.15,flag.set)
        timer.start()
        start=time.monotonic()
        try:
            with self.assertRaisesRegex(ProtocolError,'UNKNOWN_OUTCOME'):
                exchange([sys.executable,'-c','import time;time.sleep(30)'],{'task_id':'t'},timeout=5,cancelled=flag)
            self.assertLess(time.monotonic()-start,2)
        finally:
            timer.join()
