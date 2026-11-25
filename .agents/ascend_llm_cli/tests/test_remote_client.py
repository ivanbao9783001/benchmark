import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock,patch
from deploy_eval_v2.remote_client import relay,main
from deploy_eval_v2.protocol import ProtocolError,envelope,encode
from test_cli import Output


class RemoteClientTests(unittest.TestCase):
    def task_root(self, folder, snapshot, events):
        from deploy_eval_v2.durable import atomic_json
        root=Path(folder)
        atomic_json(root/'.runtime/identity.json',dict(task_id='t',node_root=str(root)))
        atomic_json(root/'.runtime/process.json',dict(pid=999999,start_ticks='1',boot_id='b',session=999999))
        if snapshot is not None:
            atomic_json(root/'state.json',snapshot)
        (root/'task.jsonl').write_bytes(b''.join(encode(event)+b'\n' for event in events))
        return root

    def test_offline_status_reads_persisted_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            root=self.task_root(folder,
                dict(protocol_version='1.0',task_id='t',task_version=3,state='CANCELLED',
                     operation_id=None,service_id=None,task_cancelled=True,next_cursor=3),
                [dict(task_id='t',seq=1,type='task.initialized',status='EXTERNAL_READY'),
                 dict(task_id='t',seq=2,type='operation.failed',status='FAILED'),
                 dict(task_id='t',seq=3,type='operation.cancelled',status='CANCELLED')])
            factory=MagicMock(side_effect=ConnectionRefusedError())
            with patch('deploy_eval_v2.launcher.process_identity',side_effect=FileNotFoundError()):
                response=relay(root,dict(protocol_version='1.0',task_id='t',kind='get-status'),
                               socket_factory=factory)
            self.assertEqual(response['task_state'],'CANCELLED')
            self.assertEqual(response['next_cursor'],3)
            self.assertIs(response['offline'],True)
            self.assertEqual(response['source'],'state.json')
            self.assertIs(response['stale'],False)
            self.assertEqual(response['runtime'],'STOPPED')

    def test_stale_snapshot_defers_to_terminal_journal_tail(self):
        with tempfile.TemporaryDirectory() as folder:
            root=self.task_root(folder,
                dict(protocol_version='1.0',task_id='t',task_version=2,state='FAILED',
                     operation_id='op',service_id=None,task_cancelled=False,next_cursor=2),
                [dict(task_id='t',seq=1,type='task.initialized',status='EXTERNAL_READY'),
                 dict(task_id='t',seq=2,type='operation.failed',status='FAILED'),
                 dict(task_id='t',seq=3,type='operation.cancelled',status='CANCELLED')])
            factory=MagicMock(side_effect=ConnectionRefusedError())
            response=relay(root,dict(protocol_version='1.0',task_id='t',kind='get-status'),
                           socket_factory=factory)
            self.assertIs(response['stale'],True)
            self.assertEqual(response['journal_next_cursor'],3)
            self.assertEqual(response['last_event_type'],'operation.cancelled')
            self.assertEqual(response['task_state'],'CANCELLED')
            self.assertIsNone(response['operation_id'])

    def test_offline_status_ignores_diagnostic_records(self):
        with tempfile.TemporaryDirectory() as folder:
            root=self.task_root(folder,
                dict(protocol_version='1.0',task_id='t',task_version=3,state='CANCELLED',
                     operation_id=None,service_id=None,task_cancelled=True,next_cursor=3),
                [dict(task_id='t',seq=1,type='task.initialized',status='EXTERNAL_READY'),
                 dict(record_type='diagnostic',event='process.start',message='x'),
                 dict(task_id='t',seq=2,type='operation.failed',status='FAILED'),
                 dict(task_id='t',seq=3,type='operation.cancelled',status='CANCELLED'),
                 dict(record_type='diagnostic',event='process.exit',message='y')])
            factory=MagicMock(side_effect=ConnectionRefusedError())
            response=relay(root,dict(protocol_version='1.0',task_id='t',kind='get-status'),
                           socket_factory=factory)
            self.assertIs(response['stale'],False)
            self.assertEqual(response['task_state'],'CANCELLED')

    def test_offline_status_stays_unknown_without_proof(self):
        with tempfile.TemporaryDirectory() as folder:
            factory=MagicMock(side_effect=ConnectionRefusedError())
            request=dict(protocol_version='1.0',task_id='t',kind='get-status')
            # No durable identity: another task's files must never be reported.
            with self.assertRaisesRegex(ProtocolError,'UNKNOWN_OUTCOME'):
                relay(Path(folder),request,socket_factory=factory)
            state=dict(protocol_version='1.0',task_id='t',task_version=1,state='READY',
                       operation_id=None,service_id=None,task_cancelled=False,next_cursor=1)
            events=[dict(task_id='t',seq=1,type='deployment.ready',status='READY')]
            # Identity without a snapshot.
            root=self.task_root(folder,None,events)
            with self.assertRaisesRegex(ProtocolError,'UNKNOWN_OUTCOME'):
                relay(root,request,socket_factory=factory)
            # Snapshot for another task.
            root=self.task_root(folder,dict(state,task_id='other'),events)
            with self.assertRaisesRegex(ProtocolError,'UNKNOWN_OUTCOME'):
                relay(root,request,socket_factory=factory)
            # Snapshot ahead of the journal: evidence cannot be reconciled.
            root=self.task_root(folder,dict(state,next_cursor=5),events)
            with self.assertRaisesRegex(ProtocolError,'UNKNOWN_OUTCOME'):
                relay(root,request,socket_factory=factory)

    def test_stopped_task_artifacts_remain_readable_with_identity(self):
        import tempfile
        from pathlib import Path
        from deploy_eval_v2.durable import atomic_json
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            atomic_json(root/'.runtime/identity.json',dict(task_id='t',node_root=str(root)))
            (root/'task.jsonl').write_bytes(b'saved evidence\n')
            request=dict(protocol_version='1.0',task_id='t',kind='read-result',artifact_ref='task.jsonl')
            factory=MagicMock(side_effect=ConnectionRefusedError())
            response=relay(root,request,socket_factory=factory)
            self.assertEqual(response['content'],'saved evidence\n')
            request['task_id']='foreign'
            with self.assertRaises(ProtocolError):
                relay(root,request,socket_factory=factory)

    def setUp(self):
        # Transport mocks exercise framing on Windows without claiming a real
        # Unix socket connection. Linux integration remains a separate gate.
        setting=patch('deploy_eval_v2.remote_client.socket.AF_UNIX',1,create=True)
        setting.start()
        self.addCleanup(setting.stop)
    def factory(self,data):
        factory=MagicMock()
        client=factory.return_value.__enter__.return_value
        client.makefile.return_value=io.BytesIO(data)
        return factory,client

    def test_query_budget_and_identity(self):
        request=dict(protocol_version='1.0',task_id='t',kind='wait-event',after_seq=7,wait_ms=5)
        factory,client=self.factory(encode(envelope('t',7,'WAIT_TIMEOUT'))+b'\n')
        result=relay('/work/t',request,socket_factory=factory)
        self.assertEqual(result['next_cursor'],7)
        self.assertEqual(json.loads(client.sendall.call_args.args[0]),request)
        for raw in (b'x'*8193+b'\n',encode(envelope('other',7))+b'\n',b'{}'):
            factory,_=self.factory(raw)
            with self.assertRaisesRegex(ProtocolError,'TRANSPORT_PROTOCOL_ERROR'):
                relay('/work/t',request,socket_factory=factory)

    def test_socket_failure_is_unknown_not_operation_failure(self):
        factory,client=self.factory(b'')
        client.connect.side_effect=ConnectionRefusedError()
        with self.assertRaisesRegex(ProtocolError,'UNKNOWN_OUTCOME'):
            relay('/work/t',dict(protocol_version='1.0',task_id='t',kind='get-status'),socket_factory=factory)

    def test_main_returns_bounded_error_preserving_cursor_without_traceback(self):
        output=Output();input_=MagicMock()
        input_.buffer=io.BytesIO(encode(dict(protocol_version='1.0',task_id='t',kind='wait-event',after_seq=9))+b'\n')
        with patch('deploy_eval_v2.remote_client.sys.argv',['client','--root','/work/t']),patch('deploy_eval_v2.remote_client.sys.stdin',input_),patch('deploy_eval_v2.remote_client.sys.stdout',output),patch('deploy_eval_v2.remote_client.relay',side_effect=ProtocolError('UNKNOWN_OUTCOME')):
            code=main()
        result=json.loads(output.buffer.getvalue())
        self.assertEqual(code,3)
        self.assertEqual(result['next_cursor'],9)
        self.assertLessEqual(len(output.buffer.getvalue()),8193)
