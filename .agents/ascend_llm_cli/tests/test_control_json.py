"""control.json delivery-root contract at the public command boundary."""
import unittest
from unittest.mock import Mock,patch
from deploy_eval_v2.publication_cli import publish,start_task
from deploy_eval_v2.protocol import ProtocolError


def publication():
    return dict(task_ref='t',runtime_archive='/tmp/deliver/t/runtime.zip',
                input_directory='/tmp/deliver/t/frozen',runtime_sha256='a'*64,bundle_sha256='b'*64)


def start():
    return dict(task_ref='t',runtime_sha256='a'*64,bundle_sha256='b'*64,nonce='c'*64)


def config(**overrides):
    value=dict(host='host-a',root='/work/t',allowed_root='/work',task_root='/deliver/t')
    value.update(overrides)
    return {'tasks':{'t':value}}


class ControlConfigTests(unittest.TestCase):
    def test_publish_requires_a_delivery_root(self):
        with self.assertRaisesRegex(ProtocolError,'delivery root'):
            publish(publication(),config(task_root=None),publisher=Mock())

    def test_start_requires_a_delivery_root(self):
        with self.assertRaisesRegex(ProtocolError,'delivery root'):
            start_task(start(),config(task_root=None),transport=Mock())

    def test_delivery_root_must_not_be_the_node_root(self):
        with self.assertRaisesRegex(ProtocolError,'delivery root'):
            publish(publication(),config(task_root='/work/t'),publisher=Mock())

    def test_valid_delivery_root_reaches_payload_validation(self):
        with patch('deploy_eval_v2.publication_cli.verify_frozen',
                   side_effect=ProtocolError('INVALID_INPUT: frozen payload')):
            with self.assertRaisesRegex(ProtocolError,'frozen'):
                publish(publication(),config(),publisher=Mock())
