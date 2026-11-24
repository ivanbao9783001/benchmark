import csv
import json
import os
from pathlib import Path
import py_compile
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from deploy_eval_v2 import accuracy_evidence, aisbench_cli
from deploy_eval_v2.benchmark_preflight import inspect_environment
from deploy_eval_v2.evaluate import prepare_case
from deploy_eval_v2.event_journal import Journal
from deploy_eval_v2.plan_model import normalize_external_plan, validate_eval_bindings
from deploy_eval_v2.protocol import ProtocolError
from deploy_eval_v2.result_contract import evaluation_result
from test_plan_model import eval_bindings, external_plan


def accuracy_plan():
    plan = external_plan()
    plan['cases'] = [dict(case_id='acc', mode='acc', dataset_id='gpqa', concurrency=8,
                          max_out_len=512, timeout_seconds=3600)]
    return plan


def accuracy_bindings():
    bindings = eval_bindings()
    bindings['testers']['tester']['datasets'] = {
        'gpqa': dict(type='aisbench_task', task='gpqa_gen_0_shot_str', path='/data/gpqa')}
    return bindings


def accuracy_dataset(directory, samples=198):
    dataset = aisbench_cli.accuracy_dataset_for(
        dict(type='aisbench_task', task='gpqa_gen_0_shot_str', path=str(directory)))
    dataset['samples'] = samples
    return dataset


def write_gpqa(directory, samples=198, columns=12):
    path = Path(directory)/'gpqa_diamond.csv'
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream)
        header = ['']*78
        header[7] = 'Question'
        writer.writerow(header)
        for index in range(samples):
            row = (['']*7 + [f'Question {index}\nsecond line', 'A', 'B', 'C', 'D'] + ['']*20)[:columns]
            writer.writerow(row)
    return path


def write_gsm8k(directory, test_samples=1319, train_samples=7473):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for split, count in (('train', train_samples), ('test', test_samples)):
        with (directory/(split+'.jsonl')).open('w', encoding='utf-8') as stream:
            for index in range(count):
                row = dict(question=f'Question {index}?', answer=f'work\n#### {index}')
                stream.write(json.dumps(row)+'\n')
    return directory


def build_native_run(root, samples=198, accuracy=42.5, success=True, logs=True, summary=True,
                     output_tokens=None, truncated=0, abbr='gpqa_diamond_0_shot_str'):
    run = Path(root)/'outputs'/'20261118_120000'
    predictions = run/'predictions'/'service'/(abbr+'.jsonl')
    predictions.parent.mkdir(parents=True)
    with predictions.open('w', encoding='utf-8') as stream:
        for index in range(samples):
            row = dict(success=success, uuid=str(index), origin_prompt='q',
                       prediction='A', gold='A')
            if output_tokens is not None:
                row['output_tokens'] = output_tokens if index < truncated else 1
            stream.write(json.dumps(row)+'\n')
    results = run/'results'/'service'/(abbr+'.json')
    results.parent.mkdir(parents=True)
    results.write_text(json.dumps({'accuracy': accuracy}), encoding='utf-8')
    if logs:
        for name in ('infer', 'eval'):
            path = run/'logs'/name/'service'/(abbr+'.out')
            path.parent.mkdir(parents=True)
            path.write_text('native log', encoding='utf-8')
    if summary:
        path = run/'summary'/'summary_20261118_120000.csv'
        path.parent.mkdir(parents=True)
        path.write_text('summary', encoding='utf-8')
    return run


class AccuracyContractTests(unittest.TestCase):
    def test_accuracy_case_is_a_sibling_schema_branch(self):
        plan = accuracy_plan()
        normalized, derived = normalize_external_plan(plan)
        self.assertEqual(derived, [])
        self.assertEqual(normalized['cases'][0]['mode'], 'acc')
        for field, value in (('input_tokens', 128), ('steady_state', False), ('num_warmups', 1)):
            broken = accuracy_plan()
            broken['cases'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ProtocolError):
                normalize_external_plan(broken)
        broken = accuracy_plan()
        del broken['cases'][0]['max_out_len']
        with self.assertRaises(ProtocolError):
            normalize_external_plan(broken)
        mini = accuracy_plan()
        mini['cases'][0]['num_prompts'] = 30
        self.assertEqual(normalize_external_plan(mini)[0]['cases'][0]['num_prompts'], 30)
        # No static upper bound: the bound is checked against counted data later.
        large = accuracy_plan()
        large['cases'][0]['num_prompts'] = 199
        self.assertEqual(normalize_external_plan(large)[0]['cases'][0]['num_prompts'], 199)
        broken = accuracy_plan()
        broken['cases'][0]['num_prompts'] = 0
        with self.assertRaises(ProtocolError):
            normalize_external_plan(broken)
        perf = external_plan()
        perf['cases'][0]['mode'] = 'perf'
        self.assertEqual(normalize_external_plan(perf)[0]['cases'][0]['mode'], 'perf')

    def test_accuracy_binding_is_a_sibling_schema_branch(self):
        plan, bindings = accuracy_plan(), accuracy_bindings()
        validate_eval_bindings(plan, bindings)
        gsm8k = accuracy_bindings()
        gsm8k['testers']['tester']['datasets']['gpqa'] = dict(
            type='aisbench_task', task='gsm8k_gen_0_shot_cot_str', path='/data/gsm8k')
        validate_eval_bindings(plan, gsm8k)
        for task in ('gpqa_gen_0_shot_cot_chat_prompt', 'gpqa_ppl_0_shot_str',
                     'gsm8k_gen_0_shot_cot_chat_prompt'):
            broken = accuracy_bindings()
            broken['testers']['tester']['datasets']['gpqa']['task'] = task
            with self.subTest(task=task), self.assertRaises(ProtocolError):
                validate_eval_bindings(plan, broken)
        broken = accuracy_bindings()
        broken['testers']['tester']['datasets']['gpqa']['path'] = 'relative/gpqa'
        with self.assertRaises(ProtocolError):
            validate_eval_bindings(plan, broken)

    def test_modes_never_borrow_each_others_dataset_branch(self):
        synthetic = eval_bindings()
        synthetic['testers']['tester']['datasets'] = {'gpqa': {'type': 'aisbench_synthetic'}}
        with self.assertRaisesRegex(ProtocolError, 'accuracy case'):
            validate_eval_bindings(accuracy_plan(), synthetic)
        plan = external_plan()
        plan['cases'][0]['dataset_id'] = 'gpqa'
        with self.assertRaisesRegex(ProtocolError, 'performance case'):
            validate_eval_bindings(plan, accuracy_bindings())

    def test_accuracy_native_config_and_command(self):
        plan, bindings = accuracy_plan(), accuracy_bindings()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            case, _, argv = prepare_case(plan, bindings, 'acc', root/'case', check_files=False)
            text = (root/'case'/'aisbench-config.py').read_text(encoding='utf-8')
            py_compile.compile(str(root/'case'/'aisbench-config.py'), doraise=True)
            self.assertIn("abbr='gpqa_diamond_0_shot_str'", text)
            self.assertIn("name='gpqa_diamond.csv'", text)
            self.assertIn("path='/data/gpqa'", text)
            self.assertIn('GPQAEvaluator', text)
            self.assertIn('first_option_postprocess', text)
            self.assertIn('with read_base():', text)
            self.assertIn('from ais_bench.benchmark.configs.summarizers.example import summarizer', text)
            self.assertIn('max_out_len=512', text)
            self.assertIn('batch_size=8', text)
            self.assertNotIn('test_range', text)
            self.assertNotIn('--num-prompts', argv)
            self.assertEqual(argv, ['/venv/bin/ais_bench', str(root/'case'/'aisbench-config.py'),
                                    '--mode', 'all', '--work-dir', str(root/'case'/'outputs')])
            expectations = json.loads((root/'case'/'native-expectations.json').read_text())
            self.assertEqual(expectations, [['service', 'gpqa_diamond_0_shot_str']])
            mini = accuracy_plan()
            mini['cases'][0]['num_prompts'] = 30
            _, _, mini_argv = prepare_case(mini, bindings, 'acc', root/'mini', check_files=False)
            self.assertEqual(mini_argv[-2:], ['--num-prompts', '30'])
            mini_text = (root/'mini'/'aisbench-config.py').read_text(encoding='utf-8')
            self.assertNotIn('test_range', mini_text)

    def test_gsm8k_native_config_and_command(self):
        plan = accuracy_plan()
        bindings = eval_bindings()
        bindings['testers']['tester']['datasets'] = {
            'gpqa': dict(type='aisbench_task', task='gsm8k_gen_0_shot_cot_str', path='/data/gsm8k')}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            case, _, argv = prepare_case(plan, bindings, 'acc', root/'case', check_files=False)
            text = (root/'case'/'aisbench-config.py').read_text(encoding='utf-8')
            py_compile.compile(str(root/'case'/'aisbench-config.py'), doraise=True)
            self.assertIn('from ais_bench.benchmark.datasets import GSM8KDataset, gsm8k_postprocess, '
                          'gsm8k_dataset_postprocess, Gsm8kEvaluator', text)
            self.assertIn("abbr='gsm8k'", text)
            self.assertIn("path='/data/gsm8k'", text)
            self.assertNotIn('name=', text)
            self.assertIn("input_columns=['question']", text)
            self.assertIn("template=\"Question: {question}\\nLet's think step by step\\nAnswer:\"", text)
            self.assertIn('Gsm8kEvaluator', text)
            self.assertIn("pred_role='BOT'", text)
            self.assertIn("pred_postprocessor=dict(type=gsm8k_postprocess)", text)
            self.assertIn('dataset_postprocessor', text)
            self.assertNotIn('test_range', text)
            self.assertEqual(argv, ['/venv/bin/ais_bench', str(root/'case'/'aisbench-config.py'),
                                    '--mode', 'all', '--work-dir', str(root/'case'/'outputs')])
            expectations = json.loads((root/'case'/'native-expectations.json').read_text())
            self.assertEqual(expectations, [['service', 'gsm8k']])
            mini = accuracy_plan()
            mini['cases'][0]['num_prompts'] = 30
            _, _, mini_argv = prepare_case(mini, bindings, 'acc', root/'mini', check_files=False)
            self.assertEqual(mini_argv[-2:], ['--num-prompts', '30'])

    def test_dataset_row_count_uses_csv_reader(self):
        with tempfile.TemporaryDirectory() as folder:
            write_gpqa(folder)
            dataset = accuracy_dataset(folder)
            self.assertEqual(aisbench_cli.check_accuracy_dataset(dataset)['samples'], 198)
            checked = aisbench_cli.check_accuracy_dataset(dataset, 30)
            self.assertEqual(checked['requested_samples'], 30)
            with self.assertRaisesRegex(ValueError, r'\[1, 198\]'):
                aisbench_cli.check_accuracy_dataset(dataset, 199)
            write_gpqa(folder, columns=10)
            with self.assertRaisesRegex(ValueError, 'question and choices'):
                aisbench_cli.check_accuracy_dataset(dataset)

    def test_dataset_size_is_counted_instead_of_hardcoded(self):
        with tempfile.TemporaryDirectory() as folder:
            write_gpqa(folder, samples=250)
            dataset = accuracy_dataset(folder, samples=0)
            counted = aisbench_cli.check_accuracy_dataset(dataset, 250)
            self.assertEqual(counted['samples'], 250)
            self.assertEqual(counted['requested_samples'], 250)
            with self.assertRaisesRegex(ValueError, r'\[1, 250\]'):
                aisbench_cli.check_accuracy_dataset(dataset, 251)

    def test_gsm8k_counts_test_split_and_requires_both_files(self):
        with tempfile.TemporaryDirectory() as folder:
            write_gsm8k(folder, test_samples=12, train_samples=5)
            dataset = aisbench_cli.accuracy_dataset_for(
                dict(type='aisbench_task', task='gsm8k_gen_0_shot_cot_str', path=folder))
            counted = aisbench_cli.check_accuracy_dataset(dataset, 4)
            self.assertEqual(counted['samples'], 12)
            self.assertEqual(counted['requested_samples'], 4)
            self.assertEqual(counted['split'], 'test')
            self.assertTrue(counted['path'].endswith('test.jsonl'))
            with self.assertRaisesRegex(ValueError, r'\[1, 12\]'):
                aisbench_cli.check_accuracy_dataset(dataset, 13)
            (Path(folder)/'train.jsonl').unlink()
            with self.assertRaisesRegex(ValueError, 'train.jsonl'):
                aisbench_cli.check_accuracy_dataset(dataset)

    def test_gsm8k_rejects_rows_without_question_or_answer(self):
        with tempfile.TemporaryDirectory() as folder:
            write_gsm8k(folder, test_samples=2, train_samples=1)
            with (Path(folder)/'test.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps({'question': 'q'})+'\n')
            dataset = aisbench_cli.accuracy_dataset_for(
                dict(type='aisbench_task', task='gsm8k_gen_0_shot_cot_str', path=folder))
            with self.assertRaisesRegex(ValueError, 'input/output columns'):
                aisbench_cli.check_accuracy_dataset(dataset)

    def test_preflight_checks_native_imports_and_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            executable = root/'ais_bench'
            executable.write_text('# test executable')
            executable.chmod(0o700)
            write_gpqa(root)
            tester = dict(aisbench=str(executable), python=sys.executable, model_path=str(root),
                          datasets={'gpqa': dict(type='aisbench_task', task='gpqa_gen_0_shot_str',
                                                 path=str(root))})
            distribution = Mock(metadata={'Name': 'ais_bench'}, version='test-version',
                                entry_points=[Mock(group='console_scripts', name='ais_bench')])
            distribution.entry_points[0].name = 'ais_bench'
            with patch('deploy_eval_v2.benchmark_preflight.sysconfig.get_path', return_value=folder), \
                 patch('deploy_eval_v2.benchmark_preflight.importlib.metadata.distributions',
                       return_value=[distribution]), \
                 patch('deploy_eval_v2.benchmark_preflight.check_accuracy_imports',
                       return_value={'dataset': 'GPQADataset'}) as imports:
                result = inspect_environment(tester, accuracy_plan()['cases'][0])
                mini = inspect_environment(
                    tester, dict(accuracy_plan()['cases'][0], num_prompts=30))
            self.assertEqual(result['dataset']['samples'], 198)
            self.assertEqual(mini['dataset']['requested_samples'], 30)
            self.assertEqual(imports.call_count, 2)


class AccuracyTruncationTests(unittest.TestCase):
    def _measure(self, samples, truncated, limit=512, num_prompts=None):
        with tempfile.TemporaryDirectory() as folder:
            build_native_run(folder, samples=samples, output_tokens=limit, truncated=truncated)
            dataset = accuracy_dataset(folder)
            case = dict(max_out_len=limit)
            if num_prompts is not None:
                case['num_prompts'] = num_prompts
            validated = accuracy_evidence.validate_accuracy_result(folder, case, dataset)
            return validated, accuracy_evidence.summarize(folder, case, dataset, validated)

    def test_truncation_at_or_above_five_percent_is_unverified(self):
        validated, accuracy = self._measure(198, 10)
        self.assertEqual(validated['truncated_samples'], 10)
        self.assertGreaterEqual(accuracy['truncation_ratio'], 0.05)
        self.assertEqual(accuracy['status'], 'UNVERIFIED')
        self.assertEqual(accuracy['validity_reasons'], ['ACC_OUTPUT_TRUNCATED'])
        self.assertTrue(accuracy['evidence_complete'])

    def test_truncation_below_five_percent_keeps_verified_with_limitation(self):
        validated, accuracy = self._measure(198, 9)
        self.assertEqual(validated['truncated_samples'], 9)
        self.assertLess(accuracy['truncation_ratio'], 0.05)
        self.assertEqual(accuracy['status'], 'VERIFIED')
        self.assertEqual(accuracy['validity_reasons'], [])
        self.assertEqual(accuracy['limitations'], ['9/198 predictions reached max_out_len=512'])

    def test_no_truncation_has_no_limitation(self):
        validated, accuracy = self._measure(198, 0)
        self.assertEqual(validated['truncated_samples'], 0)
        self.assertEqual(accuracy['status'], 'VERIFIED')
        self.assertEqual(accuracy['limitations'], [])

    def test_subset_uses_effective_sample_count(self):
        validated, accuracy = self._measure(30, 0, num_prompts=30)
        self.assertEqual(validated['num_samples'], 30)
        self.assertEqual(validated['dataset_samples'], 198)
        self.assertTrue(validated['subset'])
        self.assertEqual(accuracy['status'], 'VERIFIED')
        self.assertEqual(accuracy['num_samples'], 30)
        self.assertEqual(accuracy['dataset_samples'], 198)
        self.assertTrue(accuracy['subset'])
        self.assertIn('subset: first 30/198', accuracy['limitations'][0])
        self.assertIn('subset: first 30/198', accuracy['scope'])

    def test_subset_truncation_ratio_uses_subset_denominator(self):
        validated, accuracy = self._measure(30, 2, num_prompts=30)
        self.assertEqual(validated['truncated_samples'], 2)
        self.assertEqual(accuracy['truncation_ratio'], round(2/30, 6))
        self.assertEqual(accuracy['status'], 'UNVERIFIED')
        self.assertEqual(accuracy['validity_reasons'], ['ACC_OUTPUT_TRUNCATED'])


class AccuracyEvidenceTests(unittest.TestCase):
    def test_native_artifacts_are_verified_and_metric_forwarded(self):
        with tempfile.TemporaryDirectory() as folder:
            build_native_run(folder)
            dataset = accuracy_dataset(folder)
            validated = accuracy_evidence.validate_accuracy_result(folder, {}, dataset)
            self.assertEqual(validated['status'], 'PASSED')
            self.assertEqual(validated['num_samples'], 198)
            self.assertEqual(validated['missing_artifacts'], [])
            self.assertTrue((Path(folder)/'validated.json').is_file())
            accuracy = accuracy_evidence.summarize(folder, {}, dataset, validated)
            self.assertEqual((accuracy['status'], accuracy['value'], accuracy['unit'], accuracy['num_samples']),
                             ('VERIFIED', 42.5, 'percent', 198))
            report = json.loads((Path(folder)/'accuracy-evidence.json').read_text())
            self.assertEqual(report['value'], 42.5)

    def test_gsm8k_evidence_forwards_native_metric(self):
        with tempfile.TemporaryDirectory() as folder:
            build_native_run(folder, samples=12, accuracy=33.3, abbr='gsm8k')
            dataset = aisbench_cli.accuracy_dataset_for(
                dict(type='aisbench_task', task='gsm8k_gen_0_shot_cot_str', path='/data/gsm8k'))
            dataset['samples'] = 1319
            case = dict(max_out_len=512, num_prompts=12)
            validated = accuracy_evidence.validate_accuracy_result(folder, case, dataset)
            self.assertEqual((validated['num_samples'], validated['dataset_samples'], validated['subset']),
                             (12, 1319, True))
            accuracy = accuracy_evidence.summarize(folder, case, dataset, validated)
            self.assertEqual((accuracy['value'], accuracy['task'], accuracy['status']),
                             (33.3, 'gsm8k_gen_0_shot_cot_str', 'VERIFIED'))
            self.assertIn('GSM8K subset: first 12/1319', accuracy['scope'])
            report = json.loads((Path(folder)/'accuracy-evidence.json').read_text())
            self.assertEqual(report['dataset'], 'gsm8k')

    def test_missing_soft_artifacts_downgrade_but_keep_metric(self):
        with tempfile.TemporaryDirectory() as folder:
            build_native_run(folder, logs=False, summary=False)
            dataset = accuracy_dataset(folder)
            validated = accuracy_evidence.validate_accuracy_result(folder, {}, dataset)
            self.assertEqual(sorted(validated['missing_artifacts']),
                             ['ACC_EVAL_LOG_MISSING', 'ACC_INFER_LOG_MISSING', 'ACC_SUMMARY_MISSING'])
            accuracy = accuracy_evidence.summarize(folder, {}, dataset, validated)
            self.assertEqual(accuracy['status'], 'UNVERIFIED')
            self.assertEqual(accuracy['value'], 42.5)

    def test_hard_native_failures_raise_their_codes(self):
        cases = [
            (dict(success=False), 'ACC_PREDICTIONS_INVALID'),
            (dict(samples=197), 'ACC_PREDICTIONS_COUNT_MISMATCH'),
            (dict(accuracy=101), 'ACC_VALUE_INVALID'),
        ]
        for options, code in cases:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as folder:
                build_native_run(folder, **options)
                dataset = accuracy_dataset(folder)
                with self.assertRaisesRegex(ProtocolError, code):
                    accuracy_evidence.validate_accuracy_result(folder, {}, dataset)
        with tempfile.TemporaryDirectory() as folder:
            build_native_run(folder)
            (Path(folder)/'outputs'/'20261118_120000'/'results'/'service'/'gpqa_diamond_0_shot_str.json').unlink()
            with self.assertRaisesRegex(ProtocolError, 'ACC_RESULTS_MISSING'):
                accuracy_evidence.validate_accuracy_result(folder, {}, accuracy_dataset(folder))
        with tempfile.TemporaryDirectory() as folder:
            build_native_run(folder)
            (Path(folder)/'outputs'/'20261118_120001').mkdir()
            with self.assertRaisesRegex(ProtocolError, 'ACC_EXP_DIR_AMBIGUOUS'):
                accuracy_evidence.validate_accuracy_result(folder, {}, accuracy_dataset(folder))

    def test_predictions_symlink_outside_operation_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as outside:
            run = build_native_run(folder)
            target = Path(outside)/'predictions.jsonl'
            target.write_text('', encoding='utf-8')
            predictions = run/'predictions'/'service'/'gpqa_diamond_0_shot_str.jsonl'
            predictions.unlink()
            try:
                os.symlink(target, predictions)
            except OSError:
                self.skipTest('symlink unavailable')
            with self.assertRaisesRegex(ProtocolError, 'ACC_PREDICTIONS_INVALID'):
                accuracy_evidence.validate_accuracy_result(folder, {}, accuracy_dataset(folder))


class AccuracyResultContractTests(unittest.TestCase):
    def bind(self, value):
        return evaluation_result(value, task_id='t', operation_id='op', service_id='s', case_id='acc')

    def accuracy(self, status='VERIFIED'):
        return dict(schema_version='1.0', status=status, task='gpqa_gen_0_shot_str',
                    dataset='gpqa_diamond', metric='accuracy', unit='percent', value=42.5,
                    num_samples=198, scope='test')

    def test_accuracy_result_is_validated_by_its_own_branch(self):
        result = self.bind(dict(evaluation_type='acc', execution_status='SUCCEEDED',
                                evidence_status='COMPLETE', accuracy_validity='VERIFIED',
                                accuracy=self.accuracy()))
        self.assertEqual(result['evaluation_type'], 'acc')
        with self.assertRaises(ProtocolError):
            self.bind(dict(evaluation_type='acc', execution_status='SUCCEEDED',
                           evidence_status='PARTIAL', accuracy_validity='VERIFIED',
                           accuracy=self.accuracy()))
        with self.assertRaises(ProtocolError):
            self.bind(dict(evaluation_type='acc', execution_status='SUCCEEDED',
                           evidence_status='COMPLETE', accuracy_validity='VERIFIED'))

    def test_perf_results_keep_their_existing_contract(self):
        self.assertEqual(self.bind(dict(execution_status='SUCCEEDED', evidence_status='PARTIAL',
                                        performance_validity='UNVERIFIED'))['evaluation_type'], 'perf')

    def test_accuracy_event_carries_accuracy_validity(self):
        with tempfile.TemporaryDirectory() as folder:
            journal = Journal(folder, 't')
            event = journal.append('evaluation.completed', 'op', 1, 'EVALUATED', dict(
                case_id='acc', result_ref='results/eval.json', execution_status='SUCCEEDED',
                accuracy_validity='VERIFIED'))
            self.assertEqual(event['data']['accuracy_validity'], 'VERIFIED')
            with self.assertRaises(ProtocolError):
                journal.append('evaluation.completed', 'op', 2, 'EVALUATED', dict(
                    case_id='acc', result_ref='results/eval.json', execution_status='SUCCEEDED'))
