import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator
import deploy_eval_v2


class TesterEnvironmentContractTest(unittest.TestCase):
    def test_both_stage_contracts_accept_environment_and_reject_invalid_values(self):
        root = Path(deploy_eval_v2.__file__).parent / 'schemas'
        for name in ('deploy-bindings.schema.json', 'eval-bindings.schema.json'):
            with self.subTest(schema=name):
                schema = json.loads((root / name).read_text())
                validator = Draft202012Validator({
                    '$ref': '#/$defs/tester', '$defs': schema['$defs']})
                tester = dict(host='host', work_root='/work', python='/bin/python3',
                              aisbench='/bin/ais_bench', model_path='/model',
                              datasets={'synthetic': {'type': 'aisbench_synthetic'}},
                              environment={'PYTHONPATH': '/opt/aisbench'})
                self.assertFalse(list(validator.iter_errors(tester)))
                for invalid in ({'INVALID-NAME': 'x'}, {'PATH': 1}, {'PATH': 'a\x00b'}):
                    tester['environment'] = invalid
                    self.assertTrue(list(validator.iter_errors(tester)))

    def test_self_node_container_may_omit_image(self):
        root = Path(deploy_eval_v2.__file__).parent / 'schemas'
        schema = json.loads((root / 'deploy-bindings.schema.json').read_text())
        validator = Draft202012Validator(
            schema['properties']['nodes']['additionalProperties']['properties']['container'])
        self.assertFalse(list(validator.iter_errors({'id': 'a'*64, 'self': True})))
        self.assertFalse(list(validator.iter_errors(
            {'id': 'a'*64, 'image': 'sha256:'+'b'*64, 'self': True})))
        self.assertTrue(list(validator.iter_errors({'id': 'a'*64})))
        self.assertTrue(list(validator.iter_errors({'id': 'a'*64, 'image': 'tag'})))

    def test_tester_container_may_omit_image(self):
        root = Path(deploy_eval_v2.__file__).parent / 'schemas'
        for name in ('deploy-bindings.schema.json', 'eval-bindings.schema.json'):
            with self.subTest(schema=name):
                schema = json.loads((root / name).read_text())
                validator = Draft202012Validator(
                    schema['$defs']['tester']['properties']['container'])
                self.assertFalse(list(validator.iter_errors({'id': 'a'*64})))
