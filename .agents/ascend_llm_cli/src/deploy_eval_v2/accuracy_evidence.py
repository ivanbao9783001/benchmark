"""Native AISBench accuracy evidence.

The evaluator metric is authoritative; this module verifies that the native
predictions, results and logs exist as configured, then forwards the metric
unchanged. It never recomputes accuracy from predictions.
"""
import hashlib
import json
import math
from pathlib import Path
from . import aisbench_cli
from .durable import atomic_json
from .json_io import load_json, unique_object, reject_constant
from .process_guard import remaining, Cancelled
from .protocol import ProtocolError

LINE_LIMIT = 1024*1024
INFER_LOG_MISSING = 'ACC_INFER_LOG_MISSING'
EVAL_LOG_MISSING = 'ACC_EVAL_LOG_MISSING'
SUMMARY_MISSING = 'ACC_SUMMARY_MISSING'


def _confined(root, path, code):
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ProtocolError(code)
    return path


def _scratch_dir(folder):
    """The native CLI writes exactly one timestamped run directory."""
    root = Path(folder).resolve()
    outputs = root/'outputs'
    runs = sorted(path for path in outputs.iterdir() if path.is_dir()) if outputs.is_dir() else []
    if len(runs) != 1:
        raise ProtocolError('ACC_EXP_DIR_AMBIGUOUS')
    return root, _confined(root, runs[0], 'ACC_EXP_DIR_AMBIGUOUS')


def _read_predictions(path, expected):
    checksum = hashlib.sha256()
    seen = 0
    with path.open('rb') as stream:
        while True:
            line = stream.readline(LINE_LIMIT+1)
            if not line:
                break
            checksum.update(line)
            if len(line) > LINE_LIMIT:
                raise ProtocolError('ACC_PREDICTIONS_INVALID')
            if not line.strip():
                continue
            try:
                row = json.loads(line, object_pairs_hook=unique_object, parse_constant=reject_constant)
                if (not isinstance(row, dict) or row.get('success') is not True or
                        'gold' not in row or 'prediction' not in row):
                    raise ValueError('prediction row')
            except (ValueError, KeyError, TypeError) as error:
                raise ProtocolError('ACC_PREDICTIONS_INVALID') from error
            seen += 1
            if seen > expected:
                raise ProtocolError('ACC_PREDICTIONS_COUNT_MISMATCH')
    if seen != expected:
        raise ProtocolError('ACC_PREDICTIONS_COUNT_MISMATCH')
    return seen, checksum.hexdigest()


def _read_accuracy(path):
    try:
        values = load_json(path, 1024*1024)
        value = values['accuracy']
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError('accuracy type')
        value = float(value)
        if not math.isfinite(value) or not 0 <= value <= 100:
            raise ValueError('accuracy range')
        return value
    except (ProtocolError, ValueError, KeyError, TypeError) as error:
        raise ProtocolError('ACC_VALUE_INVALID') from error


def _present(path):
    return path.is_file() and not path.is_symlink()


def validate_accuracy_result(folder, case, dataset, *, deadline=None, cancelled=None):
    """Structurally validate native accuracy artifacts; hard failures raise."""
    def check():
        if deadline is not None:
            remaining(deadline)
        if cancelled is not None and cancelled.is_set():
            raise Cancelled('Accuracy validation cancelled')

    check()
    root, run = _scratch_dir(folder)
    abbr = dataset['dataset_abbr']
    predictions = _confined(root, run/'predictions'/aisbench_cli.MODEL_ABBR/(abbr+'.jsonl'),
                            'ACC_PREDICTIONS_INVALID')
    if not predictions.is_file():
        raise ProtocolError('ACC_PREDICTIONS_MISSING')
    check()
    num_samples, checksum = _read_predictions(predictions, dataset['samples'])
    results = _confined(root, run/'results'/aisbench_cli.MODEL_ABBR/(abbr+'.json'),
                        'ACC_RESULTS_MISSING')
    if not results.is_file():
        raise ProtocolError('ACC_RESULTS_MISSING')
    accuracy = _read_accuracy(results)
    check()
    missing = []
    for log, code in ((run/'logs'/'infer', INFER_LOG_MISSING), (run/'logs'/'eval', EVAL_LOG_MISSING)):
        path = log/aisbench_cli.MODEL_ABBR/(abbr+'.out')
        if not (_present(path) and path.resolve().is_relative_to(root)):
            missing.append(code)
    summary = run/'summary'
    summaries = [path for pattern in ('*.csv', '*.txt', '*.md')
                 for path in (summary.glob('summary_'+pattern) if summary.is_dir() else [])]
    if not any(_present(path) and path.resolve().is_relative_to(root) for path in summaries):
        missing.append(SUMMARY_MISSING)
    validated = dict(schema_version='1.0', status='PASSED', mode='acc',
                     task=dataset['task'], dataset=dataset['dataset_name'],
                     num_samples=num_samples, all_requests_succeeded=True,
                     accuracy=accuracy, predictions_ref=predictions.relative_to(root).as_posix(),
                     predictions_sha256=checksum, missing_artifacts=missing,
                     scope='native predictions/results/log presence; evaluator metric forwarded')
    atomic_json(root/'validated.json', validated)
    return validated


def summarize(folder, case, dataset, validated, *, deadline=None, cancelled=None):
    """Write the accuracy evidence file and return the result accuracy block."""
    if cancelled is not None and cancelled.is_set():
        raise Cancelled('Accuracy evidence cancelled')
    if deadline is not None:
        remaining(deadline)
    missing = list(validated.get('missing_artifacts', []))
    status = 'VERIFIED' if validated.get('status') == 'PASSED' and not missing else 'UNVERIFIED'
    root = Path(folder).resolve()
    report = dict(schema_version='1.0', status=status, task=dataset['task'],
                  dataset=dataset['dataset_name'], metric='accuracy', unit='percent',
                  value=validated['accuracy'], num_samples=validated['num_samples'],
                  missing_artifacts=missing,
                  scope='GPQA-Diamond full; native evaluator metric; no baseline or gate')
    atomic_json(root/'accuracy-evidence.json', report)
    return dict(schema_version='1.0', status=status, task=dataset['task'],
                dataset=dataset['dataset_name'], metric='accuracy', unit='percent',
                value=validated['accuracy'], num_samples=validated['num_samples'],
                scope=report['scope'])
