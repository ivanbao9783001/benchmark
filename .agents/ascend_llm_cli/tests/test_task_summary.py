import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deploy_eval_v2.cli import main
from deploy_eval_v2.protocol import ProtocolError, encode
from deploy_eval_v2.task_summary import summarize
from jsonschema import Draft202012Validator

SCHEMA = json.loads((Path(__file__).resolve().parents[1] /
                     'src/deploy_eval_v2/schemas/task-summary.schema.json').read_text(encoding='utf-8'))

PLAN = {
    'task_id': 't', 'intent': 'deploy',
    'deployment': {
        'id': 'pd-qwen3-06b',
        'model': {'id': 'qwen3-06b', 'served_name': 'Qwen3-0.6B'},
        'runtime': {'mode': 'existing_container'},
        'instances': [{'id': 'prefill-0', 'role': 'prefill'},
                      {'id': 'decode-0', 'role': 'decode'}],
        'kv_transfer': {'connector': 'MooncakeConnectorV1'},
    },
}
BINDINGS = {
    'task_id': 't',
    'nodes': {
        'n1': {'host': '179.165.1.3', 'machine_id': 'm1',
               'container': {'id': 'c1', 'image': 'img:1', 'self': True}},
        'n2': {'host': '179.166.1.4', 'machine_id': 'm2',
               'container': {'id': 'c2', 'image': 'img:1'}},
    },
}
COMPILED = {
    'schema_version': '1.0', 'model': 'Qwen3-0.6B', 'model_id': 'qwen3-06b',
    'topology': {
        'endpoint': {'host': '179.165.1.3', 'port': 18080},
        'groups': {
            'n1': {'node': 'n1', 'self': True, 'npu_ids': [0], 'ports': [8020, 18080],
                   'health_urls': ['http://179.165.1.3:18080/healthcheck'],
                   'device_map': {'declared': {'0': 0}, 'basis': 'declared'},
                   'container': {'id': 'c1', 'image': 'img:1'}},
            'n2': {'node': 'n2', 'npu_ids': [0], 'ports': [8021],
                   'health_urls': ['http://179.166.1.4:8021/health'],
                   'device_map': {'declared': {'0': 0}, 'basis': 'declared'},
                   'container': {'id': 'c2', 'image': 'img:1'}},
        },
    },
}
EXTERNAL_PLAN = {
    'task_id': 't', 'intent': 'external_eval',
    'service': {'service_id': 'svc-9', 'endpoint': 'http://179.165.1.3:18080',
                'model': 'qwen3-0.6b', 'health_path': '/healthcheck'},
    'cases': [{'case_id': 'case-1'}],
}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


def fixtures(root):
    write(root/'frozen/plan.json', PLAN)
    write(root/'frozen/bindings.json', BINDINGS)
    write(root/'frozen/compiled.json', COMPILED)
    write(root/'frozen/review.json', {'status': 'APPROVED'})
    write(root/'top_input.json', {'action': 'deploy_only', 'topology': 'pd'})
    write(root/'results/deploy-001/stage-result.json', {
        'schema_version': '1.0', 'execution_status': 'SUCCEEDED',
        'service_id': 'svc-1', 'endpoint': {'host': '179.165.1.3', 'port': 18080},
        'model': 'Qwen3-0.6B', 'cleanup_status': 'NOT_REQUESTED',
        'containers': {'n1': 'c1', 'n2': 'c2'},
    })
    write(root/'results/eval-001/stage-result.json', {
        'schema_version': '1.0', 'execution_status': 'SUCCEEDED',
        'service_id': 'svc-1', 'case_id': 'case-1', 'evaluation_type': 'perf',
        'evidence_status': 'COMPLETE', 'performance_validity': 'VERIFIED',
    })
    write(root/'results/release-001/stage-result.json', {
        'schema_version': '1.0', 'service_id': 'svc-1', 'cleanup_status': 'VERIFIED',
        'resources': {'n1': {'cleanup_status': 'VERIFIED'},
                      'n2': {'cleanup_status': 'VERIFIED'}},
    })
    (root/'progress.log').write_text('\n'.join([
        'PHASE prepare-inputs 0 1.5 2026-11-26T00:00:00Z',
        'PHASE package-runtime 0 2.0 2026-11-26T00:00:02Z',
        'PHASE draft-review 0 0.5 2026-11-26T00:00:03Z',
        'PHASE freeze-inputs 0 1.0 2026-11-26T00:00:04Z',
        'PHASE check-ready 0 0.8 2026-11-26T00:00:05Z',
        'PHASE publish-task 0 4.0 2026-11-26T00:00:09Z',
        'PHASE start-task 0 2.0 2026-11-26T00:00:11Z',
        'PHASE deploy 0 60.0 2026-11-26T00:01:11Z',
        'PHASE release 0 5.0 2026-11-26T00:01:16Z',
        'PHASE stop-runtime 0 1.0 2026-11-26T00:01:17Z',
    ]) + '\n', encoding='utf-8')


def binding(root):
    return {'task_root': str(root), 'root': '/work/t', 'allowed_root': '/work',
            'host': 'example.invalid'}


class TaskSummaryTests(unittest.TestCase):
    def test_summary_exposes_identity_phases_and_results(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'t'
            fixtures(root)
            response = summarize(binding(root), 't', status={
                'task_state': 'RELEASED', 'task_version': 7, 'operation_id': None})
        self.assertEqual(response['status'], 'OK')
        self.assertEqual(response['schema_version'], 'task-summary/1')
        summary = response['summary']
        task = summary['task']
        self.assertEqual(task['model'], 'Qwen3-0.6B')
        self.assertEqual(task['model_id'], 'qwen3-06b')
        self.assertEqual(task['connector'], 'MooncakeConnectorV1')
        self.assertEqual(task['topology'], 'pd')
        self.assertEqual(task['runtime_mode'], 'existing_container')
        self.assertEqual(task['node_count'], 2)
        self.assertEqual(task['nodes'][0]['device_map']['basis'], 'declared')
        self.assertEqual(summary['state']['source'], 'get-status')
        self.assertEqual(summary['state']['task_state'], 'RELEASED')
        phases = {item['name']: item for item in summary['phases']}
        for name in ('prepare-inputs', 'freeze-inputs', 'deploy', 'release', 'stop-runtime'):
            self.assertEqual(phases[name]['status'], 'succeeded')
        self.assertEqual(phases['eval']['status'], 'succeeded')
        self.assertEqual(summary['service']['service_id'], 'svc-1')
        self.assertEqual(summary['evaluation']['performance_validity'], 'VERIFIED')
        self.assertEqual(summary['cleanup']['status'], 'VERIFIED')
        self.assertEqual(summary['cleanup']['source'], 'release')
        self.assertEqual(summary['limitations'], [])
        self.assertLessEqual(len(encode(response)), 8192)
        Draft202012Validator(SCHEMA).validate(response)

    def test_external_eval_frozen_bundle_needs_no_compiled_topology(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'t'
            write(root/'frozen/plan.json', EXTERNAL_PLAN)
            write(root/'frozen/bindings.json', {'task_id': 't', 'testers': {
                't': {'host': '179.165.1.3', 'work_root': '/opt/pd-work'}}})
            write(root/'frozen/review.json', {'status': 'APPROVED'})
            write(root/'top_input.json', {})
            write(root/'results/eval-001/stage-result.json', {
                'schema_version': '1.0', 'execution_status': 'SUCCEEDED',
                'service_id': 'svc-9', 'case_id': 'case-1', 'evaluation_type': 'perf',
                'evidence_status': 'PARTIAL', 'performance_validity': 'VERIFIED',
            })
            (root/'progress.log').write_text(
                'PHASE eval 0 165.0 2026-11-26T00:00:00Z\n', encoding='utf-8')
            response = summarize(binding(root), 't')
        summary = response['summary']
        task = summary['task']
        self.assertEqual(task['model'], 'qwen3-0.6b')
        self.assertEqual(task['model_id'], 'qwen3-0.6b')
        self.assertEqual(task['action'], 'eval_only')
        self.assertEqual(summary['limitations'], [])
        phases = {item['name']: item for item in summary['phases']}
        self.assertEqual(phases['deploy']['status'], 'not_applicable')
        self.assertEqual(phases['release']['status'], 'not_applicable')
        self.assertEqual(phases['eval']['status'], 'succeeded')
        self.assertEqual(phases['freeze-inputs']['status'], 'succeeded')
        self.assertEqual(summary['evaluation']['performance_validity'], 'VERIFIED')
        Draft202012Validator(SCHEMA).validate(response)

    def test_deploy_only_marks_eval_not_applicable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'t'
            write(root/'frozen/plan.json', PLAN)
            write(root/'frozen/bindings.json', BINDINGS)
            write(root/'frozen/compiled.json', COMPILED)
            write(root/'frozen/review.json', {'status': 'APPROVED'})
            write(root/'top_input.json', {'action': 'deploy_only'})
            (root/'progress.log').write_text(
                'PHASE deploy 0 60.0 2026-11-26T00:00:00Z\n', encoding='utf-8')
            response = summarize(binding(root), 't')
        phases = {item['name']: item for item in response['summary']['phases']}
        self.assertEqual(phases['deploy']['status'], 'succeeded')
        self.assertEqual(phases['eval']['status'], 'not_applicable')
        self.assertEqual(phases['release']['status'], 'unknown')

    def test_deploy_only_action_read_from_v2_top_input(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'t'
            write(root/'frozen/plan.json', PLAN)
            write(root/'frozen/bindings.json', BINDINGS)
            write(root/'frozen/compiled.json', COMPILED)
            write(root/'frozen/review.json', {'status': 'APPROVED'})
            write(root/'top_input.json', {'schema_version': 'top-input/2',
                                          'task': {'id': 't', 'action': 'deploy_only'}})
            (root/'progress.log').write_text(
                'PHASE deploy 0 60.0 2026-11-26T00:00:00Z\n', encoding='utf-8')
            response = summarize(binding(root), 't')
        summary = response['summary']
        self.assertEqual(summary['task']['action'], 'deploy_only')
        phases = {item['name']: item for item in summary['phases']}
        self.assertEqual(phases['deploy']['status'], 'succeeded')
        self.assertEqual(phases['eval']['status'], 'not_applicable')

    def test_missing_inputs_and_failed_phase_stay_visible(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'t'
            root.mkdir()
            (root/'progress.log').write_text(
                'PHASE deploy 1 30.0 2026-11-26T00:00:00Z\n', encoding='utf-8')
            response = summarize(binding(root), 't',
                                 status_error={'code': 'UNKNOWN_OUTCOME'})
        summary = response['summary']
        phases = {item['name']: item for item in summary['phases']}
        self.assertEqual(phases['deploy']['status'], 'failed')
        self.assertEqual(phases['freeze-inputs']['status'], 'unknown')
        self.assertEqual(summary['state']['source'], 'unavailable')
        self.assertEqual(summary['state']['error']['code'], 'UNKNOWN_OUTCOME')
        self.assertIn('frozen inputs not found under task_root', summary['limitations'])
        self.assertTrue(any('get-status unavailable' in item
                            for item in summary['limitations']))

    def test_cli_degrades_when_status_is_unreachable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'t'
            fixtures(root)
            config = Path(folder)/'control.json'
            config.write_text(json.dumps({'tasks': {'t': {
                'host': 'example.invalid', 'root': '/work/t',
                'allowed_root': '/work', 'task_root': str(root)}}}), encoding='utf-8')
            output = io.BytesIO()

            class Stdout:
                buffer = output

            with patch('deploy_eval_v2.cli.sys.stdout', Stdout()), \
                    patch('deploy_eval_v2.cli.exchange',
                          side_effect=ProtocolError('UNKNOWN_OUTCOME')):
                code = main(['--config', str(config), 'task-summary', '--task-ref', 't'])
            response = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(response['status'], 'OK')
        self.assertEqual(response['summary']['state']['source'], 'unavailable')
        self.assertEqual(response['summary']['task']['model_id'], 'qwen3-06b')
        self.assertLessEqual(len(output.getvalue()), 8193)


if __name__ == '__main__':
    unittest.main()
