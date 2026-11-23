"""Delivery root vs node runtime root separation (Plan A directory contract)."""
import tempfile
import unittest
from pathlib import Path,PurePosixPath
from unittest.mock import patch
from deploy_eval_v2.publication_cli import task_binding,task_delivery_root
from deploy_eval_v2.readiness import task_root_writable
from deploy_eval_v2.protocol import ProtocolError

NODE=PurePosixPath('/work/t')


def config(**overrides):
    value=dict(host='host-a',root='/work/t',allowed_root='/work',task_root='/deliver/t')
    value.update(overrides)
    return {'tasks':{'t':value}}


class DeliveryRootTests(unittest.TestCase):
    def test_delivery_root_must_be_named_after_the_task(self):
        self.assertEqual(task_delivery_root(config()['tasks']['t'],'t',NODE),PurePosixPath('/deliver/t'))
        for value in ('/deliver/other','/deliver','relative/t','/deliver/../t','/deliver/t\n'):
            with self.subTest(value=value),self.assertRaisesRegex(ProtocolError,'delivery root'):
                task_delivery_root(dict(config()['tasks']['t'],task_root=value),'t',NODE)

    def test_delivery_root_never_overlaps_the_node_root(self):
        for value in ('/work/t','/work/t/deliver','/work'):
            with self.subTest(value=value),self.assertRaisesRegex(ProtocolError,'delivery root'):
                task_delivery_root(dict(config()['tasks']['t'],task_root=value),'t',NODE)

    def test_task_binding_requires_delivery_root_only_for_control_entries(self):
        with self.assertRaisesRegex(ProtocolError,'delivery root'):
            task_binding(config(task_root=None),'t',delivery=True)
        nested={'tasks':{'target':dict(host='host-b',root='/tasks/t',allowed_root='/tasks')}}
        self.assertEqual(task_binding(nested,'target')['root'],'/tasks/t')

    def test_node_root_must_stay_inside_allowed_root(self):
        for value in ('/work','/other/t'):
            with self.subTest(value=value),self.assertRaisesRegex(ProtocolError,'task root'):
                task_binding(config(root=value),'t',delivery=True)

    def test_writable_probe_walks_to_an_existing_parent(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertTrue(task_root_writable(Path(folder)/'nested'/'t'))
            with patch('deploy_eval_v2.readiness.os.access',return_value=False):
                self.assertFalse(task_root_writable(Path(folder)/'nested'/'t'))
