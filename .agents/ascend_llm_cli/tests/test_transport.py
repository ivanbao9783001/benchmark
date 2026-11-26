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
            self.run_child('raise SystemExit(255)')

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
