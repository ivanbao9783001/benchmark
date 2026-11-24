"""Build AISBench native configs for the two peer evaluation modes.

``perf`` measures request performance against generated or bound load;
``acc`` runs a native AISBench accuracy task and reports its evaluator metric.
Both branches own their config and command lines; the shared lifecycle lives in
``evaluate``.
"""
import csv
import json
import hashlib
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

PERF_MODE = 'perf'
ACC_MODE = 'acc'
MODEL_ABBR = 'service'
GPQA_PROMPT = ('What is the correct answer to this question: {question}\nChoices:\n'
               '(A){A}\n(B){B}\n(C){C}\n(D){D}\n'
               'Format your response as follows: "The correct answer is (insert answer here)"')
GSM8K_PROMPT = "Question: {question}\nLet's think step by step\nAnswer:"
DATASETS_MODULE = 'ais_bench.benchmark.datasets'
POSTPROCESS_MODULE = 'ais_bench.benchmark.utils.postprocess.text_postprocessors'
# Native accuracy tasks are selected by name; AISBench owns their semantics.
# Entries describe the native task only: sample counts come from the bound data
# at runtime, so adding or resizing a dataset never edits this table.
SUPPORTED_TASKS = {
    'gpqa_gen_0_shot_str': dict(
        layout='single_csv',
        file='gpqa_diamond.csv',
        dataset_class='GPQADataset',
        evaluator='GPQAEvaluator',
        imports=((DATASETS_MODULE, ('GPQADataset', 'GPQAEvaluator')),
                 (POSTPROCESS_MODULE, ('first_option_postprocess',))),
        dataset_abbr='gpqa_diamond_0_shot_str',
        dataset_name='gpqa_diamond',
        label='GPQA-Diamond',
        reader_columns=('question', 'A', 'B', 'C', 'D'),
        output_column='answer',
        prompt=GPQA_PROMPT,
        pred_postprocessor="dict(type=first_option_postprocess, options='ABCD')",
    ),
    'gsm8k_gen_0_shot_cot_str': dict(
        layout='split_jsonl',
        split_files=dict(train='train.jsonl', test='test.jsonl'),
        evaluated_split='test',
        dataset_class='GSM8KDataset',
        evaluator='Gsm8kEvaluator',
        imports=((DATASETS_MODULE, ('GSM8KDataset', 'gsm8k_postprocess',
                                    'gsm8k_dataset_postprocess', 'Gsm8kEvaluator')),),
        dataset_abbr='gsm8k',
        dataset_name='gsm8k',
        label='GSM8K',
        reader_columns=('question',),
        output_column='answer',
        prompt=GSM8K_PROMPT,
        pred_role='BOT',
        pred_postprocessor='dict(type=gsm8k_postprocess)',
        dataset_postprocessor='dict(type=gsm8k_dataset_postprocess)',
    ),
}


def task_mode(case):
    """Return the validated benchmark mode; unknown modes never fall back."""
    mode = case.get('mode', PERF_MODE)
    if mode not in (PERF_MODE, ACC_MODE):
        raise ValueError('AISBench test mode '+repr(mode)+' is reserved but not implemented; no perf fallback')
    return mode


def dataset_digest(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def synthetic_config(case):
    """Translate confirmed targets only; AISBench owns all sample generation."""
    from decimal import Decimal
    import math
    size=case.get('input_tokens');count=case.get('formal_requests')
    ratio=case.get('shared_prefix_ratio',0)
    if type(size) is not int or not 1 <= size < 2**20:
        raise ValueError('AISBench synthetic requires input_tokens in [1, 2**20)')
    if type(count) is not int or not 1 <= count < 2**20:
        raise ValueError('AISBench synthetic requires formal_requests in [1, 2**20)')
    if type(ratio) not in (int,float) or not math.isfinite(ratio) or not 0 <= ratio <= 1:
        raise ValueError('Invalid synthetic shared_prefix_ratio')
    prefix=int(Decimal(size)*Decimal(str(ratio)))
    return {'Type':'tokenid','RequestCount':count,'TrustRemoteCode':False,
            'TokenIdConfig':{'RequestSize':size,'PrefixLen':prefix}}


def check_synthetic_support(case):
    """Check the installed AISBench implementation, without generating another sample set."""
    import inspect
    from ais_bench.benchmark.datasets import SyntheticDataset
    config=synthetic_config(case)
    SyntheticDataset._check_synthetic_config(config)
    source=inspect.getsource(SyntheticDataset.load)
    if 'PrefixLen' not in source:
        raise ValueError('Installed AISBench SyntheticDataset has no inspectable PrefixLen support')
    module=Path(inspect.getfile(SyntheticDataset))
    return {'type':'aisbench_synthetic','config':config,'module':str(module),
            'module_sha256':dataset_digest(module),'status':'CONFIG_CHECKED',
            'sample_generation':'PENDING_NATIVE_LOADER_VALIDATION'}


def audit_synthetic_result(folder, case, model_path, tokenizer=None):
    """Compatibility entry point for the bounded postflight auditor."""
    from .native_postflight import audit
    return audit(folder,case,model_path,tokenizer=tokenizer)


def dataset_for(benchmark, case, *, check_files=True):
    key = case.get('dataset_id', 'default')
    datasets = benchmark.get('datasets', {})
    if key not in datasets:
        raise ValueError('Missing benchmark.datasets binding: '+key)
    dataset = dict(datasets[key])
    if dataset.get('type') == 'aisbench_synthetic':
        if set(dataset) != {'type'}:
            raise ValueError('AISBench synthetic binding must not contain external paths or generator settings')
        return {**dataset,'config':synthetic_config(case),'input_column':'question','output_column':'answer'}
    path = Path(dataset.get('path', ''))
    absolute = path.is_absolute() if check_files else PurePosixPath(str(path).replace('\\', '/')).is_absolute()
    if not absolute or (check_files and not path.is_file()) or path.suffix.lower() not in ('.jsonl', '.csv'):
        raise ValueError('Dataset must be an existing absolute JSONL/CSV path: '+str(path))
    dataset.setdefault('input_column', 'question')
    dataset.setdefault('output_column', 'answer')
    # The binding itself is knowable even when data lives only on the remote host.
    # Do not create containers merely to discover a missing evidence path.
    manifest_value = dataset.get('generation_manifest')
    if 'shared_prefix_ratio' in case and not manifest_value:
        raise ValueError('Shared prefix case requires generation manifest evidence')
    if manifest_value:
        if not isinstance(manifest_value, str):
            raise ValueError('Generation manifest must be an absolute path string')
        manifest_absolute = (Path(manifest_value).is_absolute() if check_files else
                             PurePosixPath(manifest_value.replace('\\', '/')).is_absolute())
        if not manifest_absolute:
            raise ValueError('Generation manifest must be absolute')
    if not check_files:
        return dataset
    if dataset.get('generation_manifest'):
        manifest_path=Path(dataset['generation_manifest'])
        if not manifest_path.is_absolute():
            raise ValueError('Generation manifest must be absolute')
        from .json_io import load_json
        manifest=load_json(manifest_path, 16*1024*1024)
        if dataset_digest(path)!=manifest['dataset_sha256']:
            raise ValueError('Generated dataset digest mismatch')
        evidence=manifest['audit']
        if not evidence['exact_lengths']:
            raise ValueError('Generated dataset has unresolved token lengths')
        if case.get('input_tokens',evidence['target_tokens'])!=evidence['target_tokens']:
            raise ValueError('Generated dataset input length differs from case')
        if len(evidence['token_lengths'])<case.get('formal_requests',1):
            raise ValueError('Generated dataset has insufficient audited requests')
    if 'shared_prefix_ratio' in case and manifest['parameters']['prefix_ratio']!=case['shared_prefix_ratio']:
        raise ValueError('Generated prefix target differs from case')
    for name in ('input_column', 'output_column'):
        if not isinstance(dataset[name], str) or not dataset[name].isidentifier():
            raise ValueError('Dataset column must be an identifier: '+name)
    return dataset


def check_dataset(dataset, count):
    """Read and validate the requested slice without copying or modifying data."""
    with Path(dataset['path']).open(encoding='utf-8-sig', newline='') as stream:
        def bounded_lines():
            while True:
                line=stream.readline(1024*1024+1)
                if not line:
                    break
                if len(line.encode('utf-8'))>1024*1024:
                    raise ValueError('Dataset row exceeds 1 MiB')
                yield line
        rows = csv.DictReader(bounded_lines()) if Path(dataset['path']).suffix.lower()=='.csv' else (json.loads(line) for line in bounded_lines())
        seen = 0
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get(dataset['input_column']), str) or dataset['output_column'] not in row:
                raise ValueError('Dataset row lacks configured input/output columns')
            seen += 1
            if seen >= count: break
    if seen < count:
        raise ValueError(f'Dataset has {seen} rows; requested {count}')


def accuracy_imports(task):
    """Return the native imports a task needs; config and preflight share them."""
    return list(SUPPORTED_TASKS[task]['imports'])


def accuracy_dataset_for(dataset):
    """Resolve a native AISBench accuracy task binding to its catalog semantics."""
    if not isinstance(dataset, dict) or dataset.get('type') != 'aisbench_task':
        raise ValueError('Accuracy mode requires an aisbench_task dataset binding')
    task = dataset.get('task')
    if task not in SUPPORTED_TASKS:
        raise ValueError('Unsupported AISBench accuracy task: '+repr(task))
    spec = SUPPORTED_TASKS[task]
    directory = str(dataset.get('path', ''))
    # Bindings are POSIX paths even when the coordinator runs elsewhere.
    if not (Path(directory).is_absolute() or PurePosixPath(directory.replace('\\', '/')).is_absolute()):
        raise ValueError('AISBench task dataset path must be absolute: '+directory)
    return dict(type='aisbench_task', task=task, path=directory, directory=directory,
                layout=spec['layout'], file=spec.get('file'),
                split_files=spec.get('split_files'), evaluated_split=spec.get('evaluated_split'),
                dataset_abbr=spec['dataset_abbr'], dataset_name=spec['dataset_name'],
                label=spec['label'])


def accuracy_sample_count(dataset, case):
    """Return the samples this accuracy case will infer and evaluate.

    The dataset's sample count is counted at runtime; when it is not yet known
    only the lower bound is enforced and the caller checks the upper bound
    against the counted data.
    """
    full = dataset.get('samples')
    requested = case.get('num_prompts')
    if requested is None:
        if not isinstance(full, int) or isinstance(full, bool):
            raise ValueError('Accuracy dataset sample count is unresolved; count the bound dataset first')
        return full
    if isinstance(requested, bool) or not isinstance(requested, int) or requested < 1:
        raise ValueError('Accuracy case num_prompts must be a positive integer')
    if isinstance(full, int) and not isinstance(full, bool) and requested > full:
        raise ValueError(f'Accuracy case num_prompts must be an integer in [1, {full}]')
    return requested


def _count_accuracy_rows(dataset):
    """Count the evaluated native rows and return (data path, count)."""
    spec = SUPPORTED_TASKS[dataset['task']]
    directory = Path(dataset['directory'])
    if spec['layout'] == 'single_csv':
        path = directory/spec['file']
        seen = 0
        with path.open(encoding='utf-8-sig', newline='') as stream:
            for row in csv.reader(stream):
                if len(row) < 8:
                    raise ValueError('AISBench task dataset row is truncated')
                if row[7] == 'Question':
                    continue
                if len(row) < 12:
                    raise ValueError('AISBench task dataset row lacks question and choices')
                seen += 1
        return path, seen
    if spec['layout'] == 'split_jsonl':
        for name in spec['split_files'].values():
            if not (directory/name).is_file():
                raise ValueError(f'AISBench task {dataset["task"]} requires {name} in {directory}')
        path = directory/spec['split_files'][spec['evaluated_split']]
        column = spec['reader_columns'][0]
        seen = 0
        with path.open(encoding='utf-8-sig') as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except ValueError as error:
                    raise ValueError('AISBench task dataset row is not valid JSON') from error
                if (not isinstance(row, dict) or not isinstance(row.get(column), str) or
                        spec['output_column'] not in row):
                    raise ValueError('AISBench task dataset row lacks configured input/output columns')
                seen += 1
        return path, seen
    raise ValueError('Unsupported AISBench accuracy dataset layout: '+repr(spec['layout']))


def check_accuracy_dataset(dataset, num_prompts=None):
    """Count the bound native task at runtime; data, not a constant, sets the bound."""
    path, seen = _count_accuracy_rows(dataset)
    requested = seen if num_prompts is None else accuracy_sample_count(
        dict(dataset, samples=seen), {'num_prompts': num_prompts})
    return dict(type='aisbench_task', task=dataset['task'], path=str(path),
                samples=seen, requested_samples=requested, status='CHECKED',
                layout=dataset['layout'], split=dataset.get('evaluated_split'))


def write_accuracy_config(spec, case, dataset, service, folder):
    """Generate a self-contained native accuracy config; no install mutation."""
    task_spec = SUPPORTED_TASKS[dataset['task']]
    endpoint = urlsplit(service['endpoint'])
    if endpoint.scheme not in ('http', 'https') or not endpoint.hostname:
        raise ValueError('Invalid service endpoint')
    fields = dict(attr=MODEL_ABBR, abbr=MODEL_ABBR, path=spec['model']['path'],
                  model=spec['model']['served_name'], stream=False, request_rate=0, retry=1,
                  host_ip=endpoint.hostname, host_port=endpoint.port or (443 if endpoint.scheme == 'https' else 80),
                  url=service['endpoint'].rstrip('/')+'/', max_out_len=case['max_out_len'],
                  batch_size=case['concurrency'], trust_remote_code=False,
                  generation_kwargs={'temperature': 0, 'ignore_eos': False})
    imports = '\n'.join('from '+module+' import '+', '.join(names)
                        for module, names in task_spec['imports'])
    dataset_args = f'path={dataset["directory"]!r}'
    if task_spec.get('file'):
        dataset_args += f', name={task_spec["file"]!r}'
    eval_parts = [f'evaluator=dict(type={task_spec["evaluator"]})']
    if task_spec.get('pred_role'):
        eval_parts.append(f'pred_role={task_spec["pred_role"]!r}')
    if task_spec.get('pred_postprocessor'):
        eval_parts.append('pred_postprocessor='+task_spec['pred_postprocessor'])
    if task_spec.get('dataset_postprocessor'):
        eval_parts.append('dataset_postprocessor='+task_spec['dataset_postprocessor'])
    config = folder/'aisbench-config.py'
    text = (
        'from mmengine.config import read_base\n'
        'with read_base():\n    from ais_bench.benchmark.configs.summarizers.example import summarizer\n'
        'from ais_bench.benchmark.models import VLLMCustomAPIChat\n'
        f'{imports}\n'
        'from ais_bench.benchmark.openicl.icl_prompt_template import PromptTemplate\n'
        'from ais_bench.benchmark.openicl.icl_retriever import ZeroRetriever\n'
        'from ais_bench.benchmark.openicl.icl_inferencer import GenInferencer\n'
        'from ais_bench.benchmark.partitioners import NaivePartitioner\n'
        'from ais_bench.benchmark.runners.local import LocalRunner\n'
        'from ais_bench.benchmark.tasks import OpenICLApiInferTask\n'
        'datasets = [dict(\n'
        f'    type={task_spec["dataset_class"]}, abbr={dataset["dataset_abbr"]!r}, {dataset_args},\n'
        f'    reader_cfg=dict(input_columns={list(task_spec["reader_columns"])!r}, '
        f'output_column={task_spec["output_column"]!r}),\n'
        '    infer_cfg=dict(\n'
        f'        prompt_template=dict(type=PromptTemplate, template={task_spec["prompt"]!r}),\n'
        '        retriever=dict(type=ZeroRetriever), inferencer=dict(type=GenInferencer)),\n'
        '    eval_cfg=dict('+', '.join(eval_parts)+'))]\n'
        'models = [dict(type=VLLMCustomAPIChat, '+', '.join(f'{k}={v!r}' for k, v in fields.items())+')]\n'
        'infer = dict(partitioner=dict(type=NaivePartitioner), runner=dict(type=LocalRunner, '
        'max_num_workers=1, task=dict(type=OpenICLApiInferTask)))\n'
    )
    config.write_text(text, encoding='utf-8')
    (folder/'dataset.json').write_text(json.dumps(dataset, indent=2), encoding='utf-8')
    return config


def write_config(spec, case, service, folder, *, check_files=True):
    """Generate the native config selected by the case mode."""
    if task_mode(case) == ACC_MODE:
        dataset = accuracy_dataset_for(spec['benchmark']['datasets'][case['dataset_id']])
        if case.get('num_prompts') is not None:
            # The upper bound needs counted data; preflight enforces it in the
            # tester namespace, here only the type and lower bound are known.
            accuracy_sample_count(dataset, case)
        if check_files:
            dataset['samples'] = check_accuracy_dataset(dataset, case.get('num_prompts'))['samples']
        return write_accuracy_config(spec, case, dataset, service, folder)
    return write_perf_config(spec, case, service, folder, check_files=check_files)


def write_perf_config(spec, case, service, folder, *, check_files=True):
    require_supported_mode(case)
    dataset = dataset_for(spec['benchmark'], case, check_files=check_files)
    synthetic=dataset.get('type')=='aisbench_synthetic'
    if check_files and not synthetic:
        check_dataset(dataset, case['formal_requests'])
    endpoint = urlsplit(service['endpoint'])
    if endpoint.scheme not in ('http', 'https') or not endpoint.hostname:
        raise ValueError('Invalid service endpoint')
    summarizer = 'stable_stage' if case.get('steady_state', False) else 'default_perf'
    # AISBench 3.1 counts total attempts (range(self.retry)); one sends the
    # initial request with no internal retry. Coordinator max_retries is separate.
    fields = dict(attr='service', abbr='service', path=spec['model']['path'],
                  model=spec['model']['served_name'], stream=True, request_rate=case.get('request_rate', 0),
                  retry=1, host_ip=endpoint.hostname, host_port=endpoint.port or (443 if endpoint.scheme=='https' else 80),
                  url=service['endpoint'].rstrip('/')+'/', max_out_len=case['output_tokens'],
                  batch_size=case['concurrency'], trust_remote_code=False,
                  generation_kwargs={'temperature':0,'ignore_eos':False})
    dataset_class='SyntheticDataset' if synthetic else 'CustomDataset'
    dataset_args=(f'config={dataset["config"]!r}, model_path={spec["model"]["path"]!r}' if synthetic else
                  f'path={dataset["path"]!r}, meta_path=""')
    config = folder/'aisbench-config.py'
    text = (
        'from mmengine.config import read_base\n'
        'from ais_bench.benchmark.models import VLLMCustomAPI\n'
        f'from ais_bench.benchmark.datasets import {dataset_class}\n'
        'from ais_bench.benchmark.openicl.icl_prompt_template import PromptTemplate\n'
        'from ais_bench.benchmark.openicl.icl_retriever import ZeroRetriever\n'
        'from ais_bench.benchmark.openicl.icl_inferencer import GenInferencer\n'
        'from ais_bench.benchmark.partitioners import NaivePartitioner\n'
        'from ais_bench.benchmark.runners.local import LocalRunner\n'
        'from ais_bench.benchmark.tasks import OpenICLApiInferTask\n'
        f'with read_base():\n    from ais_bench.benchmark.configs.summarizers.perf.{summarizer} import summarizer\n'
        f'datasets = [dict(type={dataset_class}, abbr="dataset", {dataset_args}, '
        f'reader_cfg=dict(input_columns={[dataset["input_column"]]!r}, output_column={dataset["output_column"]!r}), '
        f'infer_cfg=dict(prompt_template=dict(type=PromptTemplate, template={("{"+dataset["input_column"]+"}")!r}), '
        'retriever=dict(type=ZeroRetriever), inferencer=dict(type=GenInferencer)))]\n'
        'models = [dict(type=VLLMCustomAPI, '+', '.join(f'{k}={v!r}' for k,v in fields.items())+')]\n'
        'infer = dict(partitioner=dict(type=NaivePartitioner), runner=dict(type=LocalRunner, max_num_workers=1, task=dict(type=OpenICLApiInferTask)))\n'
    )
    config.write_text(text, encoding='utf-8')
    (folder/'dataset.json').write_text(json.dumps(dataset, indent=2), encoding='utf-8')
    return config


def commands(executable, config, case, folder):
    """Return the native command line for the case mode."""
    if task_mode(case) == ACC_MODE:
        # Accuracy runs infer and evaluate in one pass; the native flag slices
        # the dataset to the first N prompts without rewriting the config.
        command = [executable, str(config), '--mode', 'all',
                   '--work-dir', str(folder/'outputs')]
        if case.get('num_prompts') is not None:
            command += ['--num-prompts', str(case['num_prompts'])]
        return [('benchmark', command)]
    return perf_commands(executable, config, case, folder)


def perf_commands(executable, config, case, folder):
    require_supported_mode(case)
    warmups = case.get('num_warmups', 1) if case.get('warmup', True) else 0
    # Normal mode writes AISBench worker logs under --work-dir; never use --debug.
    return [('benchmark', [executable, str(config), '--mode', 'perf',
             '--work-dir', str(folder/'outputs'), '--num-prompts', str(case['formal_requests']),
             '--num-warmups', str(warmups)])]


def require_supported_mode(case):
    """Task modes are independent of the coordinator FULL/scripts mode."""
    task_mode(case)


def validate_native_result(folder, case):
    """Compatibility entry point for the streaming native result validator."""
    from .native_results import validate_native_result as validate
    return validate(folder,case)
