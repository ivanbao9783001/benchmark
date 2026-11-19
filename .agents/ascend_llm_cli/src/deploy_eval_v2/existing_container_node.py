"""Borrow an operator-provided container; inspect, copy and exec only.

Nothing in this adapter creates, starts, stops or removes a container, and no
container file is deleted. Release stops exactly the process groups this task
started, recognised by an execution marker in their environment.

The owned path in docker_node.py is deliberately untouched.
"""
import json
from pathlib import Path
import re
import time
from .compiler import digest
from .docker_node import DockerNode
from .durable import atomic_json
from .process_guard import remaining, run_logged
from .protocol import ProtocolError
from .release import verify_resources_absent

MARKER = 'BENCH_TASK_ID'


def _container_unavailable(output):
    """A removed or stopped container cannot still run this task's processes."""
    text = output.lower()
    return any(token in text for token in (b'no such container', b'no such object',
                                           b'is not running'))

# Port occupancy must be observed in the container's own network namespace:
# a borrowed container may already publish host ports, which says nothing
# about the ports this task's service will bind inside it.
PORT_SCRIPT = (
    "import socket,sys\n"
    "busy=[]\n"
    "for value in sys.argv[1:]:\n"
    "    probe=socket.socket()\n"
    "    try:\n"
    "        probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)\n"
    "        probe.bind(('0.0.0.0',int(value)))\n"
    "    except OSError:\n"
    "        busy.append(value)\n"
    "    finally:\n"
    "        probe.close()\n"
    "print('FREE' if not busy else 'BUSY '+','.join(busy))\n"
)

# Runs inside the borrowed container. Only a process group whose leader carries
# the task marker (and which is not the container init group) is ours; unrelated
# container processes are never signalled, and the group is re-checked after the
# leader exits so worker children cannot survive as orphans.
STOP_SCRIPT = (
    "import os,signal,sys,time\n"
    "marker=b'BENCH_TASK_ID='+sys.argv[1].encode()\n"
    # A task owns exactly the process groups it created with setsid. Those groups
    # are recognised by their leader: children may drop the marker from their
    # environment (vLLM workers do), so requiring every member to carry it would
    # miss the real group and report a false absence.
    "def scan():\n"
    "    owners={}\n"
    "    alive={}\n"
    "    for name in os.listdir('/proc'):\n"
    "        if not name.isdigit(): continue\n"
    "        pid=int(name)\n"
    "        try:\n"
    "            with open('/proc/'+name+'/stat','rb') as stream:\n"
    "                state=stream.read().rsplit(b')',1)[1].split()[0]\n"
    "            group=os.getpgid(pid)\n"
    "        except (OSError,IndexError):\n"
    "            continue\n"
    "        if state!=b'Z': alive[group]=alive.get(group,0)+1\n"
    "        if pid!=group: continue\n"
    "        try:\n"
    "            with open('/proc/'+name+'/environ','rb') as stream:\n"
    "                values=stream.read().split(b'\\0')\n"
    "        except OSError:\n"
    "            values=[]\n"
    "        owners[group]=marker in values\n"
    "    return owners,alive\n"
    "owners,alive=scan()\n"
    "targets={group for group,marked in owners.items() if marked and group>1}\n"
    "def survivors():\n"
    "    current=scan()[1]\n"
    "    return {group for group in targets if group in current}\n"
    "def signal_groups(signum,groups):\n"
    "    for group in groups:\n"
    "        try: os.killpg(group,signum)\n"
    "        except OSError: pass\n"
    "grace=float(sys.argv[2])\n"
    "signal_groups(signal.SIGTERM,targets)\n"
    "deadline=time.monotonic()+grace\n"
    "while time.monotonic()<deadline and survivors(): time.sleep(.2)\n"
    "signal_groups(signal.SIGKILL,survivors())\n"
    "deadline=time.monotonic()+2\n"
    "while time.monotonic()<deadline and survivors(): time.sleep(.1)\n"
    "print('ABSENT' if not survivors() else 'PRESENT')\n"
)


class ExistingContainerNode(DockerNode):
    """Local adapter for a borrowed, already-running container."""

    existing_container = True

    def __init__(self, guard, container, runner=None):
        if runner is None:
            super().__init__(guard)
        else:
            super().__init__(guard, runner)
        if (not isinstance(container, dict) or
                not re.fullmatch('[a-f0-9]{64}', str(container.get('id', ''))) or
                not re.fullmatch('sha256:[a-f0-9]{64}', str(container.get('image', '')))):
            raise ProtocolError('INVALID_INPUT: existing container identity')
        self.container = dict(id=container['id'], image=container['image'])

    def inspect_running(self, cid, deadline, cancelled):
        """Verify immutable identity, expected image and running state."""
        if cid != self.container['id']:
            raise ProtocolError('INVALID_INPUT: existing container identity')
        template = ('{"Id":{{json .Id}},"Image":{{json .Image}},'
                    '"Running":{{json .State.Running}}}')
        raw = self.call(['docker', 'inspect', '--format', template, cid], deadline, cancelled)
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise ProtocolError('RECOVERY_REQUIRED: invalid container inspection') from exc
        if (data.get('Id') != self.container['id'] or data.get('Image') != self.container['image'] or
                data.get('Running') is not True):
            raise ProtocolError('INVALID_INPUT: existing container identity or running state')
        return data

    def create(self, command_id, group, deadline, cancelled):
        """Claim host resources and verify the borrowed container; create nothing."""
        resources = ['npu:'+str(n) for n in group['npu_ids']]+['tcp:'+str(p) for p in group['ports']]

        def action():
            self.guard.claim(resources)
            for device in group['npu_ids']:
                code, output, error = self.runner(['fuser', '/dev/davinci'+str(device)],
                                                  timeout=min(30, remaining(deadline)), limit=8192)
                atomic_json(self.guard.root/'probes'/(digest([command_id, device])+'.json'),
                            dict(device=device, code=code, stdout=output.decode(errors='replace'),
                                 stderr=error.decode(errors='replace')))
                if code == 0:
                    raise ProtocolError('RESOURCE_BUSY: NPU in use')
                if code != 1 or output.strip() or error.strip():
                    raise ProtocolError('RECOVERY_REQUIRED: NPU occupancy probe inconclusive')
            self.inspect_running(self.container['id'], deadline, cancelled)
            self._probe_ports(self.container['id'], group['ports'], deadline)
            # The borrowed container needs its own script/log directories.
            self.call(['docker', 'exec', self.container['id'], 'mkdir', '-p',
                       group['container_root']+'/scripts', group['container_root']+'/logs'],
                      deadline, cancelled)
            return dict(container_id=self.container['id'], image_id=self.container['image'],
                        name=self.container['id'], resources=resources)
        return self.guard.command(command_id, dict(kind='create', group=group), action)

    def start_container(self, command_id, cid, deadline, cancelled):
        """A borrowed container is already running; only re-verify it."""
        def action():
            self.inspect_running(cid, deadline, cancelled)
            return dict(container_id=cid, started=True)
        return self.guard.command(command_id, dict(kind='start', container_id=cid), action)

    def _verify_script(self, script):
        import hashlib
        path = Path(script['path'])
        if path.is_symlink() or path.stat().st_size > 4*1024*1024:
            raise ProtocolError('INVALID_INPUT: service script path')
        if hashlib.sha256(path.read_bytes()).hexdigest() != script['sha256']:
            raise ProtocolError('INVALID_INPUT: service script changed')

    def _copy_script(self, cid, script, deadline, cancelled):
        self._verify_script(script)
        self.call(['docker', 'cp', script['path'], cid+':'+script['container_path']],
                  deadline, cancelled)

    def _probe_ports(self, cid, ports, deadline, *, busy_error=True):
        """Bind inside the container namespace; the host may publish unrelated ports."""
        if not ports:
            return True
        code, output, error = self.runner(
            ['docker', 'exec', cid, 'python3', '-c', PORT_SCRIPT, *[str(port) for port in ports]],
            timeout=min(30, remaining(deadline)), limit=8192)
        if code:
            if _container_unavailable(output+error):
                return True
            raise ProtocolError('RECOVERY_REQUIRED: container port probe failed')
        lines = output.strip().splitlines()
        free = bool(lines) and lines[-1].startswith(b'FREE')
        if not free and busy_error:
            raise ProtocolError('RESOURCE_BUSY: deployment port occupied')
        return free

    def start_service(self, command_id, cid, script, deadline, cancelled):
        def action():
            self.inspect_running(cid, deadline, cancelled)
            self._copy_script(cid, script, deadline, cancelled)
            # setsid makes the script its own process group so release can stop
            # the whole vLLM tree without touching unrelated container work.
            self.call(['docker', 'exec', '-d', '-e', MARKER+'='+self.guard.owner['task_id'],
                       cid, 'setsid', 'bash', script['container_path']], deadline, cancelled)
            return dict(container_id=cid, instance=script['instance'], status='START_ACCEPTED',
                        log=script['log'])
        return self.guard.command(command_id, dict(kind='service', container_id=cid, script=script), action)

    def check_dependency(self, command_id, cid, script, deadline, cancelled):
        def action():
            self.inspect_running(cid, deadline, cancelled)
            self._copy_script(cid, script, deadline, cancelled)
            # Dependency probes stay unmarked: they are not session leaders, so
            # release must never treat their process group as this task's own.
            code = run_logged(['docker', 'exec', cid, 'bash', script['container_path']],
                              script.get('transport_log', script['log']['path']+'.transport'),
                              deadline, cancelled)
            if code:
                raise ProtocolError('INVALID_INPUT: container dependency check failed')
            return dict(container_id=cid, instance=script['instance'],
                        dependency_status='VERIFIED', log=script['log'])
        return self.guard.command(command_id, dict(kind='dependency', container_id=cid, script=script), action)

    def release_existing(self, command_id, containers, resources, evidence_path,
                         *, keep_claims=False, timeout=120):
        """Stop this task's in-container process groups; keep the container."""
        if (not containers or len(set(containers)) != len(containers) or
                any(not isinstance(cid, str) or not re.fullmatch('[a-f0-9]{64}', cid) for cid in containers) or
                not resources or any(not isinstance(r, str) or not r or len(r) > 256 for r in resources) or
                isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 3600):
            raise ProtocolError('INVALID_INPUT: release inventory')
        payload = dict(containers=sorted(containers), resources=sorted(set(resources)),
                       timeout=timeout, keep_claims=keep_claims,
                       evidence_path=str(Path(evidence_path).absolute()))

        def action():
            deadline = time.monotonic()+timeout
            results = []
            for cid in payload['containers']:
                if cid != self.container['id']:
                    results.append(dict(container_id=cid, status='UNVERIFIED',
                                        error_code='INVALID_INPUT'))
                    continue
                try:
                    results.append(dict(container_id=cid, status=self._stop_groups(cid, deadline)))
                except Exception as error:
                    results.append(dict(container_id=cid, status='UNVERIFIED',
                                        error_code=str(error).split(':', 1)[0]
                                        if isinstance(error, ProtocolError) else 'IO_ERROR'))
            stopped = all(item['status'] in ('STOPPED_VERIFIED', 'ABSENT_VERIFIED') for item in results)
            resource_results = []
            verified = stopped
            if stopped and not keep_claims:
                npu = [r for r in payload['resources'] if r.startswith('npu:')]
                ports = [int(r.split(':', 1)[1]) for r in payload['resources'] if r.startswith('tcp:')]
                if npu:
                    resource_results = verify_resources_absent(npu, self.runner, deadline)
                    verified = all(item['status'] == 'ABSENT_VERIFIED' for item in resource_results)
                free = self._probe_ports(self.container['id'], ports, deadline,
                                         busy_error=False)
                resource_results += [dict(resource='tcp:'+str(port),
                                          status='ABSENT_VERIFIED' if free else 'UNVERIFIED')
                                     for port in ports]
                verified = verified and free
            report = dict(schema_version='1.0', owner=self.guard.owner, containers=results,
                          resources=payload['resources'], resource_results=resource_results,
                          cleanup_status='VERIFIED' if verified else 'UNVERIFIED',
                          claims_released=False)
            atomic_json(Path(evidence_path), report)
            if verified:
                self.guard.release_claims(payload['resources'], resources_verified_absent=True)
                report['claims_released'] = True
                atomic_json(Path(evidence_path), report)
            return dict(cleanup_status=report['cleanup_status'],
                        claims_released=report['claims_released'], evidence_sha256=digest(report))
        return self.guard.command(command_id, payload, action)

    def _stop_groups(self, cid, deadline):
        seconds = remaining(deadline)
        grace = max(1.0, min(10.0, seconds/2))
        code, output, error = self.runner(
            ['docker', 'exec', cid, 'python3', '-c', STOP_SCRIPT,
             self.guard.owner['task_id'], str(grace)],
            timeout=min(30, seconds, grace+15), limit=8192)
        if code:
            if _container_unavailable(output+error):
                return 'ABSENT_VERIFIED'
            raise ProtocolError('RECOVERY_REQUIRED: container process stop failed')
        lines = output.strip().splitlines()
        return 'STOPPED_VERIFIED' if lines and lines[-1].strip() == b'ABSENT' else 'UNVERIFIED'
