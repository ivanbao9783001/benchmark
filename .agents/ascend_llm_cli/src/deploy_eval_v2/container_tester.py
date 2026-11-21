"""Run tester tools in an existing, identity-bound container, never manage it."""
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid

from .durable import atomic_json
from .json_io import load_json
from .process_guard import Cancelled, remaining, run_logged
from .protocol import ProtocolError
from .task_log import diagnostic


class ContainerTester:
    def __init__(self, tester, folder, deadline):
        self.tester, self.folder = tester, Path(folder)
        self.container = tester['container']['id']
        if not tester['container'].get('image'):
            raise ProtocolError('INVALID_INPUT: tester container image required')
        self.deadline = deadline
        self.host_root = Path(tester['work_root'])
        self.container_root = (Path(tester['container_work_root'])
                               if tester.get('container_work_root') else self.host_root)
        if not str(self.container_root).replace('\\', '/').startswith('/') or ':' in str(self.container_root):
            raise ProtocolError('INVALID_INPUT: tester container work root')
        self.root = self.folder.parent / ('container-tools-' + uuid.uuid4().hex)
        self.inside_root = self.inside(self.root)
        self.inside_folder = self.inside(self.folder)
        self.runtime = self.inside_root + '/runtime'
        self.source_runtime = Path(__file__).parent.parent
        item = json.loads(self.command(['docker', 'inspect', self.container]))[0]
        if (item['Id'] != self.container or item['Image'] != tester['container']['image']
                or not item['State']['Running']):
            raise ProtocolError('INVALID_INPUT: tester container identity or running state')
        # An exclusive task directory, with no image install or shared mount requirement.
        self.command(['docker', 'exec', self.container, tester['python'], '-c',
                      'import pathlib,sys; p=pathlib.Path(sys.argv[1]); p.mkdir(parents=True,exist_ok=False); (p/".runtime").mkdir()', self.inside_root])
        self.command(['docker', 'cp', str(self.source_runtime), self.container + ':' + self.runtime])
        self.command(['docker', 'exec', self.container, tester['python'], '-c',
                      'import pathlib,sys; pathlib.Path(sys.argv[1]).mkdir(parents=True,exist_ok=True)', self.inside_folder])

    def inside(self, path):
        """Map a host path under work_root to the container-visible POSIX path."""
        text = str(path).replace('\\', '/')
        host = str(self.host_root).replace('\\', '/').rstrip('/')
        if text == host:
            tail = ''
        elif text.startswith(host + '/'):
            tail = text[len(host):]
        else:
            raise ProtocolError('INVALID_INPUT: container tester path outside work root')
        return str(self.container_root).replace('\\', '/').rstrip('/') + tail

    def argument(self, value):
        """Translate a command or evidence path into the container namespace."""
        text = str(value).replace('\\', '/')
        host = str(self.folder).replace('\\', '/').rstrip('/')
        if text == host:
            return self.inside_folder
        if text.startswith(host + '/'):
            return self.inside_folder + text[len(host):]
        return text.replace(str(self.source_runtime).replace('\\', '/'), self.runtime)

    def translate(self, value):
        """Rewrite host work-root paths inside a request payload."""
        if isinstance(value, str):
            return self.map_path(value)
        if isinstance(value, list):
            return [self.translate(item) for item in value]
        if isinstance(value, dict):
            return {key: self.translate(item) for key, item in value.items()}
        return value

    def map_path(self, text):
        for host, inside in ((str(self.folder), self.inside_folder),
                             (str(self.host_root), self.container_root)):
            base = str(host).replace('\\', '/').rstrip('/')
            target = str(inside).replace('\\', '/').rstrip('/')
            if text.replace('\\', '/') == base:
                return target
            if text.replace('\\', '/').startswith(base + '/'):
                return target + text.replace('\\', '/')[len(base):]
        return text

    def command(self, argv, timeout=None):
        result = subprocess.run(argv, capture_output=True, timeout=timeout or min(30, remaining(self.deadline)))
        diagnostic(self.folder/'container.log', json.dumps(argv), event='container.command', returncode=result.returncode)
        if result.returncode:
            diagnostic(self.folder/'container.log', result.stderr.decode(errors='replace'))
            raise ProtocolError('CONTAINER_TESTER_COMMAND_FAILED')
        return result.stdout

    def run(self, argv, log, deadline, cancelled, env=None):
        self.command(['docker', 'cp', str(self.folder) + '/.', self.container + ':' + self.inside_folder])
        token = uuid.uuid4().hex
        request = self.folder / ('container-command-' + token + '.json')
        done = self.inside_root + '/' + token + '.done.json'
        cancel = self.inside_root + '/' + token + '.cancel'
        actual = [self.argument(arg) for arg in argv]
        atomic_json(request, dict(argv=actual, log=self.argument(log), timeout=remaining(deadline),
                                  environment=self.tester.get('environment', {}), done=done, cancel=cancel,
                                  root=self.inside_root))
        self.command(['docker', 'cp', str(request), self.container + ':' + self.inside(request)])
        if '--request' in argv:
            source = Path(argv[argv.index('--request') + 1])
            if source.is_file():
                # Preflight/postflight payloads carry paths; resolve them in the
                # container namespace instead of leaving host-only paths behind.
                mapped = source.with_name('container-' + source.name)
                atomic_json(mapped, self.translate(load_json(source, 1 << 20)))
                self.command(['docker', 'cp', str(mapped), self.container + ':' + self.inside(source)])
        bootstrap = 'import sys; sys.path.insert(0, ' + repr(str(self.runtime)) + '); from deploy_eval_v2.container_tester import worker; worker(sys.argv[1])'
        stop = threading.Event()
        errors = []
        def watch():
            while not stop.wait(.1):
                if cancelled.is_set():
                    try:
                        self.command(['docker', 'exec', self.container, self.tester['python'], '-c',
                                      'import pathlib,sys; pathlib.Path(sys.argv[1]).touch()', cancel], timeout=10)
                    except Exception as error:
                        errors.append(error)
                    return
        monitor = threading.Thread(target=watch, daemon=True)
        monitor.start()
        observer_error = None
        try:
            run_logged(['docker', 'exec', self.container, self.tester['python'], '-B', '-c', bootstrap, self.inside(request)],
                       self.folder/'container.log', deadline+20, threading.Event())
        except Exception as error:
            observer_error = error
        finally:
            stop.set()
            monitor.join(timeout=11)
        # Docker client termination alone never proves in-container client cleanup.
        try:
            outcome = json.loads(self.command(['docker', 'exec', self.container, self.tester['python'], '-c',
                                               'import pathlib,sys; print(pathlib.Path(sys.argv[1]).read_text())', done], timeout=10))
        except Exception as error:
            from .remote_evaluation import RemoteCleanupUnknown
            raise RemoteCleanupUnknown('Container tester cleanup unverified') from error
        # The worker has finished; persist diagnostics before acknowledging removal.
        for path in (self.inside_root + '/task.jsonl', self.inside_folder + '/task.jsonl'):
            raw = self.command(['docker', 'exec', self.container, self.tester['python'], '-c',
                                'import pathlib,sys; p=pathlib.Path(sys.argv[1]); sys.stdout.write(p.read_text() if p.exists() else "")', path], timeout=10)
            if raw:
                diagnostic(self.folder/'container.log', raw.decode(), event='container.framework.collected')
                self.command(['docker', 'exec', self.container, self.tester['python'], '-c',
                              'import pathlib,sys; pathlib.Path(sys.argv[1]).unlink()', path], timeout=10)
        self.command(['docker', 'cp', self.container + ':' + self.inside_folder + '/.', str(self.folder)], timeout=30)
        if outcome.get('cleanup_status') != 'VERIFIED' or errors:
            from .remote_evaluation import RemoteCleanupUnknown
            raise RemoteCleanupUnknown('Container tester cleanup unverified')
        if cancelled.is_set() or outcome.get('cancelled'):
            raise Cancelled('Container tester cancelled; owned process group stopped')
        if outcome.get('error'):
            raise ProtocolError('CONTAINER_TESTER_FAILED: ' + outcome['error'])
        if observer_error:
            raise observer_error
        return outcome['returncode']


def worker(request_path):
    request = json.loads(Path(request_path).read_text())
    class Flag:
        def is_set(self):
            return Path(request['cancel']).exists()
    environment = dict(os.environ)
    environment.update(request['environment'])
    outcome = dict(cleanup_status='UNVERIFIED')
    try:
        outcome['returncode'] = run_logged(request['argv'], request['log'],
                                           time.monotonic()+request['timeout'], Flag(), env=environment)
        outcome['cleanup_status'] = 'VERIFIED'
    except (Cancelled, TimeoutError) as error:
        outcome.update(cleanup_status='VERIFIED', error=type(error).__name__, cancelled=isinstance(error, Cancelled))
    except Exception as error:
        outcome['error'] = type(error).__name__
    atomic_json(Path(request['done']), outcome)
