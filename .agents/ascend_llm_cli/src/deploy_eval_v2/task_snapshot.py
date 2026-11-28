"""Read-only task state from durable snapshots, without third-party imports.

The observer must stay usable when the runtime is gone or when the interpreter
frozen into the task binding lacks the contract stack. This module therefore
depends on the standard library plus the local protocol helpers only.
"""
import json
from pathlib import Path
from .protocol import encode, envelope, ProtocolError
from .json_io import load_json, unique_object, reject_constant

TERMINALS = ('deployment.ready', 'evaluation.completed', 'operation.failed',
             'operation.cancelled', 'release.completed', 'release.unverified',
             'task.expired', 'runtime.recovery_required')


def offline_identity(root, task_id):
    """Offline gate: the durable identity must prove this exact task and root."""
    try:
        identity = load_json(Path(root)/'.runtime/identity.json')
    except (OSError, ValueError) as error:
        raise ProtocolError('UNKNOWN_OUTCOME: offline artifact identity unavailable') from error
    if (not isinstance(identity, dict) or identity.get('task_id') != task_id or
            identity.get('node_root') != str(Path(root))):
        raise ProtocolError('UNKNOWN_OUTCOME: offline artifact identity mismatch')
    return identity


def journal_tail(path, task_id):
    """Last complete event record within a bounded read; None if unprovable.

    task.jsonl also carries diagnostic records, so the tail is not necessarily
    an event; scan backwards for the newest record that proves a task event.
    """
    try:
        with path.open('rb') as stream:
            stream.seek(0, 2)
            size = stream.tell()
            chunk = min(size, 65536)
            stream.seek(size-chunk)
            data = stream.read(chunk)
    except OSError:
        return None
    lines = data.split(b'\n')
    if not data.endswith(b'\n'):
        lines = lines[:-1]
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line, object_pairs_hook=unique_object,
                                parse_constant=reject_constant)
        except ValueError:
            continue
        if (isinstance(record, dict) and record.get('task_id') == task_id and
                type(record.get('seq')) is int and record['seq'] >= 1 and
                isinstance(record.get('type'), str) and isinstance(record.get('status'), str)):
            return record
    return None


def runtime_stopped(root):
    """True only when the recorded runtime process is provably gone."""
    try:
        from .launcher import process_identity
    except ImportError:
        # Without the contract stack the process identity cannot be checked;
        # report UNOBSERVED instead of failing the whole read-only snapshot.
        return False
    try:
        record = load_json(Path(root)/'.runtime/process.json')
    except (OSError, ValueError):
        return False
    if not isinstance(record, dict) or type(record.get('pid')) is not int or record['pid'] <= 0:
        return False
    try:
        current = process_identity(record['pid'])
    except (FileNotFoundError, ProcessLookupError):
        return True
    except OSError:
        return False
    return current != record


def offline_status(root, task_id):
    """Read-only status from durable snapshots; never resumes or mutates anything.

    The journal is written before state.json, so a snapshot can lag by one event
    but never lead. Lag is reported as stale; a terminal journal tail wins.
    """
    base = Path(root)
    try:
        snapshot = load_json(base/'state.json')
    except (OSError, ValueError) as error:
        raise ProtocolError('UNKNOWN_OUTCOME: offline state snapshot unavailable') from error
    if (not isinstance(snapshot, dict) or snapshot.get('protocol_version') != '1.0' or
            snapshot.get('task_id') != task_id or
            type(snapshot.get('task_version')) is not int or snapshot['task_version'] < 0 or
            type(snapshot.get('next_cursor')) is not int or snapshot['next_cursor'] < 0 or
            not isinstance(snapshot.get('state'), str) or not snapshot['state'] or
            snapshot.get('operation_id') is not None and not isinstance(snapshot.get('operation_id'), str) or
            snapshot.get('service_id') is not None and not isinstance(snapshot.get('service_id'), str)):
        raise ProtocolError('UNKNOWN_OUTCOME: offline state snapshot invalid')
    tail = journal_tail(base/'task.jsonl', task_id)
    if tail is None or tail['seq'] < snapshot['next_cursor']:
        raise ProtocolError('UNKNOWN_OUTCOME: offline snapshot not backed by journal')
    response = envelope(task_id, snapshot['next_cursor'])
    response.update(task_version=snapshot['task_version'], operation_id=snapshot.get('operation_id'),
                    task_state=snapshot['state'], service_id=snapshot.get('service_id'),
                    result_index_ref='results/operation-index.json',
                    offline=True, source='state.json', stale=tail['seq'] > snapshot['next_cursor'],
                    runtime='STOPPED' if runtime_stopped(base) else 'UNOBSERVED')
    if response['stale']:
        response.update(journal_next_cursor=tail['seq'],
                        last_event_type=tail['type'], last_event_status=tail['status'])
        if tail['type'] in TERMINALS:
            response.update(task_state=tail['status'], operation_id=None)
    if len(encode(response)) > 8192:
        raise ProtocolError('TRANSPORT_PROTOCOL_ERROR: offline status exceeds budget')
    return response
