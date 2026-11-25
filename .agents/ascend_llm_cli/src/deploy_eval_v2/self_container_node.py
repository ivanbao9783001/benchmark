"""Borrow the container the control process itself runs in.

Selected only for the reviewed node whose container is flagged ``self``: the
runtime is already inside the borrowed container, so neither SSH nor docker is
available. Every effect mirrors ``ExistingContainerNode`` with local processes
instead of docker exec: the container is never created, started, stopped or
removed and no container file is deleted. Release stops exactly the process
groups this task started, recognised by an execution marker in the leader's
environment.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from .compiler import digest
from .durable import atomic_json
from .existing_container_node import ExistingContainerNode, MARKER, PORT_SCRIPT, STOP_SCRIPT
from .process_guard import remaining, run_logged
from .protocol import ProtocolError

DOCKERENV = Path('/.dockerenv')


class SelfContainerNode(ExistingContainerNode):
    """Local adapter for the borrowed container that hosts this process."""

    self_container = True

    def _require_inside(self):
        if not DOCKERENV.exists():
            raise ProtocolError('INVALID_INPUT: self container evidence unavailable')

    def inspect_running(self, cid, deadline, cancelled):
        """The container cannot be queried without docker; prove we are in it."""
        remaining(deadline)
        if cancelled.is_set():
            from .process_guard import Cancelled
            raise Cancelled('Operation cancelled')
        if cid != self.container['id']:
            raise ProtocolError('INVALID_INPUT: existing container identity')
        self._require_inside()
        # The image digest in the reviewed binding cannot be re-derived here.
        return dict(Id=cid, Image=self.container.get('image'), Running=True, ImageVerified=False)

    def create(self, command_id, group, deadline, cancelled):
        """Claim local resources and verify the borrowed container; create nothing."""
        resources = ['npu:'+str(n) for n in group['npu_ids']]+['tcp:'+str(p) for p in group['ports']]

        def action():
            self.guard.claim(resources)
            for device in group['npu_ids']:
                probe = self._npu_probe(device, deadline)
                atomic_json(self.guard.root/'probes'/(digest([command_id, device])+'.json'),
                            dict(device=device, **probe))
                if probe['busy'] is True:
                    raise ProtocolError('RESOURCE_BUSY: NPU in use')
                if probe['busy'] is None:
                    raise ProtocolError('RECOVERY_REQUIRED: NPU occupancy probe inconclusive')
            self._check_device_map(command_id, group, deadline, cancelled)
            self.inspect_running(self.container['id'], deadline, cancelled)
            self._probe_ports(self.container['id'], group['ports'], deadline)
            # Everything above is read-only; mark before the first mutation.
            self.guard.mark_effect_started(command_id)
            for directory in ('scripts', 'logs'):
                Path(group['container_root'], directory).mkdir(parents=True, exist_ok=True)
            # Without docker the running image cannot be re-derived, so the
            # declaration stays unverified: record that gap as evidence instead
            # of exporting a field the service contract does not define.
            atomic_json(self.guard.root/'probes'/(digest([command_id, 'container'])+'.json'),
                        dict(container_id=self.container['id'], image=self.container.get('image'),
                             image_verified=False, dockerenv=DOCKERENV.exists()))
            outcome = dict(container_id=self.container['id'],
                           name=self.container['id'], resources=resources)
            if self.container.get('image'):
                outcome['image_id'] = self.container['image']
            return outcome
        return self.guard.command(command_id, dict(kind='create', group=group), action)

    def start_container(self, command_id, cid, deadline, cancelled):
        """A borrowed container is already running; only re-verify it is this one."""
        def action():
            self.inspect_running(cid, deadline, cancelled)
            return dict(container_id=cid, started=True)
        return self.guard.command(command_id, dict(kind='start', container_id=cid), action)

    def _copy_script(self, cid, script, deadline, cancelled):
        self._verify_script(script)
        source, target = Path(script['path']), Path(script['container_path'])
        if source.resolve() != target.resolve():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)

    def _read_chip_logic_map(self, deadline):
        """Inside the borrowed container the runtime is local; no docker exec."""
        return self.runner(['npu-smi', 'info', '-m'],
                           timeout=min(30, remaining(deadline)), limit=8192)

    def _npu_probe(self, device, deadline):
        """Ask the driver, not the process table: other containers are invisible here.

        ``fuser`` is usually absent from borrowed images and a local ``/proc`` scan
        cannot see neighbouring PID namespaces, so device ownership comes from
        ``npu-smi``. Anything but an explicit no-process answer stays inconclusive.
        """
        code, output, error = self.runner(['npu-smi', 'info', '-t', 'proc-mem', '-i', str(device)],
                                          timeout=min(30, remaining(deadline)), limit=8192)
        text = output.decode(errors='replace')
        if code == 0 and 'No process in device.' in text:
            busy = False
        elif code == 0:
            busy = True
        else:
            busy = None
        return dict(code=code, stdout=text, stderr=error.decode(errors='replace'), busy=busy)

    def _verify_resources_absent(self, resources, deadline):
        """Absence evidence without fuser; ports reuse the local socket probe."""
        results = []
        for resource in resources:
            if time.monotonic() >= deadline:
                results.append(dict(resource=resource, status='UNVERIFIED'))
                continue
            probe = self._npu_probe(resource.split(':', 1)[1], deadline)
            results.append(dict(resource=resource,
                                status='ABSENT_VERIFIED' if probe['busy'] is False else 'UNVERIFIED'))
        return results

    def _probe_ports(self, cid, ports, deadline, *, busy_error=True):
        """Bind locally: this process shares the container's network namespace."""
        if not ports:
            return True
        code, output, error = self.runner(
            [sys.executable, '-c', PORT_SCRIPT, *[str(port) for port in ports]],
            timeout=min(30, remaining(deadline)), limit=8192)
        if code:
            raise ProtocolError('RECOVERY_REQUIRED: container port probe failed')
        lines = output.strip().splitlines()
        free = bool(lines) and lines[-1].startswith(b'FREE')
        if not free and busy_error:
            raise ProtocolError('RESOURCE_BUSY: deployment port occupied')
        return free

    def start_service(self, command_id, cid, script, deadline, cancelled):
        def action():
            remaining(deadline)
            if cancelled.is_set():
                from .process_guard import Cancelled
                raise Cancelled('Operation cancelled')
            self.inspect_running(cid, deadline, cancelled)
            self._copy_script(cid, script, deadline, cancelled)
            # A new session makes the script its own process group so release can
            # stop the whole vLLM tree without touching unrelated container work.
            subprocess.Popen(['bash', script['container_path']],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True,
                             env=dict(os.environ, **{MARKER: self.guard.owner['task_id']}))
            return dict(container_id=cid, instance=script['instance'], status='START_ACCEPTED',
                        log=script['log'])
        return self.guard.command(command_id, dict(kind='service', container_id=cid, script=script), action)

    def check_dependency(self, command_id, cid, script, deadline, cancelled):
        def action():
            remaining(deadline)
            if cancelled.is_set():
                from .process_guard import Cancelled
                raise Cancelled('Operation cancelled')
            self.inspect_running(cid, deadline, cancelled)
            self._copy_script(cid, script, deadline, cancelled)
            # Dependency probes stay unmarked: they are not session leaders, so
            # release must never treat their process group as this task's own.
            code = run_logged(['bash', script['container_path']],
                              script.get('transport_log', script['log']['path']+'.transport'),
                              deadline, cancelled)
            if code:
                raise ProtocolError('INVALID_INPUT: container dependency check failed')
            return dict(container_id=cid, instance=script['instance'],
                        dependency_status='VERIFIED', log=script['log'])
        return self.guard.command(command_id, dict(kind='dependency', container_id=cid, script=script), action)

    def _stop_groups(self, cid, deadline):
        seconds = remaining(deadline)
        grace = max(1.0, min(10.0, seconds/2))
        code, output, error = self.runner(
            [sys.executable, '-c', STOP_SCRIPT, self.guard.owner['task_id'], str(grace)],
            timeout=min(30, seconds, grace+15), limit=8192)
        if code:
            raise ProtocolError('RECOVERY_REQUIRED: container process stop failed')
        lines = output.strip().splitlines()
        return 'STOPPED_VERIFIED' if lines and lines[-1].strip() == b'ABSENT' else 'UNVERIFIED'
