import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from deploy_eval_v2.api import API
from deploy_eval_v2.compiler import freeze_inputs, review_inputs
from deploy_eval_v2.external_executor import ExternalExecutor
from deploy_eval_v2.plan_model import normalize_external_plan
from deploy_eval_v2.task_runtime import Runtime
from deploy_eval_v2.protocol import ProtocolError
from test_plan_model import external_plan, eval_bindings
from test_runtime import command


class ExternalRuntimeTests(unittest.TestCase):
    def setup_runtime(self, root, runner, reset=False, plan=None, bindings=None):
        plan, _ = normalize_external_plan(plan or external_plan())
        if reset:
            plan['cases'][0]['reset_cache']=True
            plan['service']['permissions']['reset_cache']=True
            plan['service']['reset_targets']=[dict(id='m',url='http://model:8000/reset_prefix_cache',
                                                  metrics_url='http://model:8000/metrics')]
        bindings = bindings or eval_bindings()
        review = dict(schema_version='1.0', status='APPROVED', unresolved=[],
                      evidence_refs=['review/input.json'], inputs=review_inputs(plan, bindings, 'a'*64))
        frozen = root/'frozen'
        ref = freeze_inputs(frozen, plan, bindings, review, 'a'*64)
        executor = ExternalExecutor(root/'task', frozen, ref['sha256'], 'a'*64,
                                    'example.invalid', case_runner=runner)
        return Runtime(root/'task', 't', executor), executor

    def test_authorized_reset_case_is_accepted_with_frozen_targets(self):
        seen=[]
        def runner(plan,*args,**kwargs):
            seen.append(plan['service']['reset_targets'])
            return dict(execution_status='SUCCEEDED',evidence_status='PARTIAL',performance_validity='UNVERIFIED')
        with tempfile.TemporaryDirectory() as folder:
            runtime,_=self.setup_runtime(Path(folder),runner,reset=True)
            response=API(runtime).submit(command('eval',runtime.version,'reset',service_id='s',case_id='c',wait_ms=1000))
            self.assertEqual(response['events'][-1]['type'],'evaluation.completed')
            self.assertEqual(seen[0][0]['url'],'http://model:8000/reset_prefix_cache')

    def test_eval_without_deploy_and_evidence_before_event(self):
        calls = []
        def runner(*args, **kwargs):
            calls.append(args[2])
            return dict(execution_status='SUCCEEDED', evidence_status='PARTIAL', performance_validity='UNVERIFIED')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runtime, executor = self.setup_runtime(root, runner)
            self.assertEqual(runtime.state, 'EXTERNAL_READY')
            req = command('eval', runtime.version, 'e', service_id='s', case_id='c', wait_ms=1000)
            response = API(runtime).submit(req)
            event = response['events'][-1]
            self.assertEqual(event['type'], 'evaluation.completed')
            result = json.loads((root/'task'/event['data']['result_ref']).read_text())
            self.assertEqual(result['operation_id'], event['operation_id'])
            restored = Runtime(root/'task', 't', executor)
            restored.submit(req)
            self.assertEqual(calls, ['c'])
            with self.assertRaisesRegex(ProtocolError, 'not owned'):
                restored.submit(command('release', restored.version, 'r', service_id='s'))

    def test_tester_container_runs_locally_without_docker(self):
        seen = {}
        def runner(plan, bindings, case_id, folder, cancelled, **kwargs):
            seen.update(kwargs)
            return dict(execution_status='SUCCEEDED', evidence_status='PARTIAL', performance_validity='UNVERIFIED')
        plan, bindings = external_plan(), eval_bindings()
        bindings['testers']['tester']['container'] = {'id': 'c'*64}
        with tempfile.TemporaryDirectory() as folder:
            runtime, _ = self.setup_runtime(Path(folder), runner, plan=plan, bindings=bindings)
            response = API(runtime).submit(command('eval', runtime.version, 'e',
                                                   service_id='s', case_id='c', wait_ms=1000))
            self.assertEqual(response['events'][-1]['type'], 'evaluation.completed')
        # Container testers execute inside their container; the coordinator no
        # longer selects docker and passes no container hint.
        self.assertEqual(seen['verified_local_host'], bindings['testers']['tester']['host'])
        self.assertNotIn('verified_local_container', seen)

    def test_invalid_case_never_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime, _ = self.setup_runtime(Path(folder), lambda *a, **k: self.fail('must not execute'))
            before = len(runtime.journal.events)
            with self.assertRaises(ProtocolError):
                runtime.submit(command('eval', runtime.version, 'e', service_id='s', case_id='missing'))
            self.assertEqual(len(runtime.journal.events), before)

    def test_changed_frozen_input_blocks_new_command(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runtime, _ = self.setup_runtime(root, lambda *a, **k: self.fail('must not execute'))
            (root/'frozen/plan.json').write_text('{}')
            before = len(runtime.journal.events)
            with self.assertRaisesRegex(ProtocolError, 'changed'):
                runtime.submit(command('eval', runtime.version, 'e', service_id='s', case_id='c'))
            self.assertEqual(len(runtime.journal.events), before)

    def test_failure_keeps_external_service_and_records_evidence(self):
        def failing(*args, **kwargs):
            raise RuntimeError('test failure')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runtime, _ = self.setup_runtime(root, failing)
            response = API(runtime).submit(command('eval', runtime.version, 'e', service_id='s', case_id='c', wait_ms=1000))
            event = response['events'][-1]
            self.assertEqual(event['type'], 'operation.failed')
            result = json.loads((root/'task'/event['data']['evidence_ref']).read_text())
            self.assertEqual(result['external_service_action'], 'NONE')
            self.assertEqual(result['cleanup_status'], 'UNVERIFIED')

    def accuracy_inputs(self):
        plan = external_plan()
        plan['cases'] = [dict(case_id='acc', mode='acc', dataset_id='gpqa', concurrency=8,
                              max_out_len=512, timeout_seconds=60)]
        bindings = eval_bindings()
        bindings['testers']['tester']['datasets'] = {
            'gpqa': dict(type='aisbench_task', task='gpqa_gen_0_shot_str', path='/data/gpqa')}
        return plan, bindings

    def test_accuracy_eval_event_carries_accuracy_validity(self):
        plan, bindings = self.accuracy_inputs()
        def runner(*args, **kwargs):
            return dict(evaluation_type='acc', execution_status='SUCCEEDED', evidence_status='COMPLETE',
                        accuracy_validity='VERIFIED',
                        accuracy=dict(schema_version='1.0', status='VERIFIED', task='gpqa_gen_0_shot_str',
                                      dataset='gpqa_diamond', metric='accuracy', unit='percent',
                                      value=42.5, num_samples=198, scope='test'))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runtime, _ = self.setup_runtime(root, runner, plan=plan, bindings=bindings)
            response = API(runtime).submit(command('eval', runtime.version, 'e', service_id='s', case_id='acc', wait_ms=1000))
            event = response['events'][-1]
            self.assertEqual(event['type'], 'evaluation.completed')
            self.assertEqual(event['data']['accuracy_validity'], 'VERIFIED')
            self.assertNotIn('performance_validity', event['data'])
            result = json.loads((root/'task'/event['data']['result_ref']).read_text())
            self.assertEqual(result['evaluation_type'], 'acc')

    def test_accuracy_failure_path_never_claims_performance(self):
        plan, bindings = self.accuracy_inputs()
        def failing(*args, **kwargs):
            raise RuntimeError('accuracy failure')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runtime, _ = self.setup_runtime(root, failing, plan=plan, bindings=bindings)
            response = API(runtime).submit(command('eval', runtime.version, 'e', service_id='s', case_id='acc', wait_ms=1000))
            event = response['events'][-1]
            self.assertEqual(event['type'], 'operation.failed')
            result = json.loads((root/'task'/event['data']['evidence_ref']).read_text())
            self.assertEqual(result['evaluation_type'], 'acc')
            self.assertEqual(result['accuracy_validity'], 'UNVERIFIED')
            self.assertNotIn('performance_validity', result)
