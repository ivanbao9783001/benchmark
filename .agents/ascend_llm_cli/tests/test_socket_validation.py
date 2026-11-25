import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from deploy_eval_v2.socket_validation import start_socket, start_installed, stop_installed
from deploy_eval_v2.protocol import ProtocolError
from deploy_eval_v2.ssh_publication import publish_task
import json
import subprocess
import sys


class SocketValidationTests(unittest.TestCase):
    def stopped_runtime(self, folder):
        from deploy_eval_v2.durable import atomic_json
        root=Path(folder)
        nonce='a'*64
        record=dict(pid=999999,start_ticks='1',boot_id='b',session=999999)
        atomic_json(root/'.runtime/launch-identity.json',dict(task_id='t',nonce=nonce))
        atomic_json(root/'.runtime/process.json',record)
        status=dict(protocol_version='1.0',task_id='t',status='EVENTS',next_cursor=4,
                    task_state='CANCELLED',offline=True)
        return root,nonce,record,status

    def test_repeated_stop_is_idempotent_only_after_process_exit(self):
        with tempfile.TemporaryDirectory() as folder:
            root,nonce,record,status=self.stopped_runtime(folder)
            with patch('deploy_eval_v2.remote_client.relay',return_value=status), \
                 patch('deploy_eval_v2.launcher.process_identity',side_effect=FileNotFoundError()):
                response=stop_installed(root,dict(task_id='t',nonce=nonce))
            self.assertIs(response['runtime_stopped'],True)
            self.assertIs(response['already_stopped'],True)
            self.assertEqual(response['process'],record)
            self.assertEqual(response['next_cursor'],4)

    def test_live_runtime_with_unreachable_socket_stays_unknown(self):
        import socket as socket_module
        with tempfile.TemporaryDirectory() as folder:
            root,nonce,record,status=self.stopped_runtime(folder)
            with patch('deploy_eval_v2.remote_client.relay',return_value=status), \
                 patch('deploy_eval_v2.launcher.process_identity',return_value=record), \
                 patch.object(socket_module,'AF_UNIX',1,create=True), \
                 patch.object(socket_module,'socket',side_effect=ConnectionRefusedError()):
                with self.assertRaisesRegex(ProtocolError,'UNKNOWN_OUTCOME'):
                    stop_installed(root,dict(task_id='t',nonce=nonce))

    def test_receiver_error_keeps_task_identity(self):
        from deploy_eval_v2 import socket_validation
        receiver=Path(socket_validation.__file__).with_name('socket_receiver.py')
        result=subprocess.run([sys.executable,str(receiver),'/'],input=json.dumps({'task_id':'t'}),text=True,capture_output=True)
        response=json.loads(result.stdout)
        self.assertEqual(response['task_id'],'t')
        self.assertEqual(response['status'],'ERROR')

    def test_invalid_nonce_precedes_upload(self):
        with patch('deploy_eval_v2.socket_validation.upload_archive') as upload:
            with self.assertRaises(ProtocolError):
                start_socket(dict(task_ref='t', nonce='bad'), {})
            upload.assert_not_called()

    def test_production_directory_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder)/'inputs').mkdir()
            with self.assertRaises(ProtocolError):
                start_installed(folder, dict(task_id='t', runtime_sha256='a'*64))

    def test_publication_preserves_task_identity(self):
        responses=[]
        def transport(argv, request, **kwargs):
            responses.append(request)
            return dict(status='EVENTS', runtime=dict(runtime_sha256='r'), inputs=dict(bundle_sha256='b'))
        with patch('deploy_eval_v2.ssh_publication.upload_archive', return_value={'sha256':'a'}):
            publish_task('host','/python','/tasks/t','r.zip','i.zip','task-identity','r','b',transport=transport)
        self.assertEqual(responses[0]['task_id'], 'task-identity')
