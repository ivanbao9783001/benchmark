"""Container/tester-local AISBench preflight. Never installs or sends model requests."""
import argparse
import importlib.metadata
from pathlib import Path
import os
import sys
import sysconfig
from . import aisbench_cli
from .durable import atomic_json
from .json_io import load_json
from .protocol import ProtocolError


def check_accuracy_imports(task):
    """Require the native task imports so an AISBench version drift fails early."""
    import importlib
    checked = []
    for module_name, names in aisbench_cli.accuracy_imports(task):
        module = importlib.import_module(module_name)
        missing = [name for name in names if not hasattr(module, name)]
        if missing:
            raise ProtocolError('INVALID_INPUT: AISBench task import missing: '+', '.join(missing))
        checked.extend(sorted(names))
    return dict(task=task, imports=checked)


def inspect_environment(tester,case,*,config_path=None):
    executable=Path(tester['aisbench'])
    python=Path(tester['python'])
    model=Path(tester['model_path'])
    if python.resolve()!=Path(sys.executable).resolve():
        raise ProtocolError('INVALID_INPUT: preflight interpreter differs from binding')
    if (not executable.is_file() or not os.access(executable,os.X_OK) or not model.is_dir() or
            executable.resolve().parent!=Path(sysconfig.get_path('scripts')).resolve()):
        raise ProtocolError('INVALID_INPUT: AISBench entry point or tokenizer assets unavailable in bound environment')
    owners=[]
    for distribution in importlib.metadata.distributions():
        if any(entry.group=='console_scripts' and entry.name==executable.name for entry in distribution.entry_points):
            owners.append(dict(name=distribution.metadata['Name'],version=distribution.version))
    if len(owners)!=1:
        raise ProtocolError('INVALID_INPUT: AISBench console entry point ownership ambiguous or absent')
    if aisbench_cli.task_mode(case) == aisbench_cli.ACC_MODE:
        data=aisbench_cli.accuracy_dataset_for(tester['datasets'][case['dataset_id']])
        dataset_evidence=aisbench_cli.check_accuracy_dataset(data, case.get('num_prompts'))
        dataset_evidence['imports']=check_accuracy_imports(data['task'])
    else:
        data=aisbench_cli.dataset_for({'datasets':tester['datasets']},case,check_files=True)
        if data.get('type')=='aisbench_synthetic':
            dataset_evidence=aisbench_cli.check_synthetic_support(case)
        else:
            aisbench_cli.check_dataset(data,case['formal_requests'])
            dataset_evidence=dict(status='CHECKED',path=data['path'])
    if config_path is not None:
        from mmengine.config import Config
        config=Config.fromfile(str(config_path))
        if not config.get('models') or not config.get('datasets'):
            raise ProtocolError('INVALID_INPUT: native benchmark config incomplete')
    return dict(schema_version='1.0',status='VERIFIED',python=str(Path(sys.executable)),
                executable=str(executable),distributions=owners,dataset=dataset_evidence,
                tokenizer_assets=str(model),scope='entry point, imports, config and dataset preflight')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--request',required=True)
    parser.add_argument('--result',required=True)
    args=parser.parse_args()
    request=load_json(args.request)
    result=inspect_environment(request['tester'],request['case'],config_path=request['config'])
    if request.get('endpoint'):
        from urllib.request import urlopen
        paths=[request['health_path']] if request.get('health_path') else ['/health','/healthcheck']
        attempts=[]
        for path in paths:
            try:
                with urlopen(request['endpoint'].rstrip('/')+path, timeout=10) as response:
                    if response.status == 200:
                        result['service_health'] = 'VERIFIED_FROM_TESTER'
                        result['service_health_path'] = path
                        if attempts:
                            result['service_health_attempts'] = attempts
                        break
                    attempts.append(dict(path=path,status=response.status))
            except OSError as exc:
                attempts.append(dict(path=path,error=type(exc).__name__))
        else:
            raise ProtocolError('INVALID_INPUT: service unhealthy from tester environment')
    atomic_json(Path(args.result),result)


if __name__=='__main__':
    main()
