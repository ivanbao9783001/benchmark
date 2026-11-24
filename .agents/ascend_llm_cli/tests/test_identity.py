import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from deploy_eval_v2 import identity
from deploy_eval_v2.protocol import ProtocolError

MACHINE_ID = '0123456789abcdef0123456789abcdef'


class MachineIdentityTests(unittest.TestCase):
    def resolve(self, content, hostname='node-a'):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'machine-id'
            if content is not None:
                path.write_text(content, encoding='ascii')
            with patch.object(identity, 'MACHINE_ID_PATH', path):
                return identity.resolve_identity(hostname_reader=lambda: hostname)

    def test_machine_id_wins_when_present(self):
        self.assertEqual(self.resolve(MACHINE_ID+'\n'),
                         {'machine_id': MACHINE_ID, 'source': 'machine-id'})

    def test_empty_machine_id_falls_back_to_the_hostname(self):
        value = self.resolve('', hostname='pd-existing-p4')
        self.assertEqual(value['source'], 'hostname-derived')
        self.assertRegex(value['machine_id'], '^[a-f0-9]{32}$')
        self.assertNotEqual(value['machine_id'], MACHINE_ID)

    def test_missing_machine_id_file_falls_back_to_the_hostname(self):
        self.assertEqual(self.resolve(None)['source'], 'hostname-derived')

    def test_hostname_derivation_is_stable_and_node_specific(self):
        self.assertEqual(identity.derive_hostname_id('node-a'),
                         identity.derive_hostname_id('node-a'))
        self.assertNotEqual(identity.derive_hostname_id('node-a'),
                            identity.derive_hostname_id('node-b'))

    def test_blank_hostname_is_an_actionable_error(self):
        with self.assertRaisesRegex(ProtocolError, 'hostname'):
            self.resolve('', hostname='   ')

    def test_identity_command_reports_the_derived_source(self):
        output = io.BytesIO()

        class Stream:
            buffer = output

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'machine-id'
            path.write_text('', encoding='ascii')
            with patch('deploy_eval_v2.cli.sys.stdout', Stream()),\
                 patch.object(identity, 'MACHINE_ID_PATH', path),\
                 patch.object(identity.socket, 'gethostname',
                              return_value='pd-existing-p4'):
                from deploy_eval_v2.cli import main
                code = main(['identity'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()),
                         {'machine_id': identity.derive_hostname_id('pd-existing-p4'),
                          'source': 'hostname-derived'})
