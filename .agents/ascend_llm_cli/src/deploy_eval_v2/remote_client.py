"""Disposable SSH-side Unix socket client; never runs stage work."""
import argparse
import json
import re
import socket
import sys
from pathlib import Path
from .protocol import encode, envelope,ProtocolError
from .json_io import unique_object,reject_constant
from .contracts import validate_command,validate_query,exit_code
from .wire_contracts import validate_response
from .runtime_socket import socket_address


def _offline_identity(root,task_id):
    """Offline gate: the durable identity must prove this exact task and root."""
    from .json_io import load_json
    try:
        identity=load_json(Path(root)/'.runtime/identity.json')
    except (OSError,ValueError) as error:
        raise ProtocolError('UNKNOWN_OUTCOME: offline artifact identity unavailable') from error
    if (not isinstance(identity,dict) or identity.get('task_id')!=task_id or
            identity.get('node_root')!=str(Path(root))):
        raise ProtocolError('UNKNOWN_OUTCOME: offline artifact identity mismatch')
    return identity


def _journal_tail(path,task_id):
    """Last complete event record within a bounded read; None if unprovable.

    task.jsonl also carries diagnostic records, so the tail is not necessarily
    an event; scan backwards for the newest record that proves a task event.
    """
    try:
        with path.open('rb') as stream:
            stream.seek(0,2)
            size=stream.tell()
            chunk=min(size,65536)
            stream.seek(size-chunk)
            data=stream.read(chunk)
    except OSError:
        return None
    lines=data.split(b'\n')
    if not data.endswith(b'\n'):
        lines=lines[:-1]
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            record=json.loads(line,object_pairs_hook=unique_object,parse_constant=reject_constant)
        except ValueError:
            continue
        if (isinstance(record,dict) and record.get('task_id')==task_id and
                type(record.get('seq')) is int and record['seq']>=1 and
                isinstance(record.get('type'),str) and isinstance(record.get('status'),str)):
            return record
    return None


def _runtime_stopped(root):
    """True only when the recorded runtime process is provably gone."""
    from .json_io import load_json
    from .launcher import process_identity
    try:
        record=load_json(Path(root)/'.runtime/process.json')
    except (OSError,ValueError):
        return False
    if not isinstance(record,dict) or type(record.get('pid')) is not int or record['pid']<=0:
        return False
    try:
        current=process_identity(record['pid'])
    except (FileNotFoundError,ProcessLookupError):
        return True
    except OSError:
        return False
    return current!=record


def _offline_status(root,task_id):
    """Read-only status from durable snapshots; never resumes or mutates anything.

    The journal is written before state.json, so a snapshot can lag by one event
    but never lead. Lag is reported as stale; a terminal journal tail wins.
    """
    from .json_io import load_json
    from .task_runtime import TERMINALS
    base=Path(root)
    try:
        snapshot=load_json(base/'state.json')
    except (OSError,ValueError) as error:
        raise ProtocolError('UNKNOWN_OUTCOME: offline state snapshot unavailable') from error
    if (not isinstance(snapshot,dict) or snapshot.get('protocol_version')!='1.0' or
            snapshot.get('task_id')!=task_id or
            type(snapshot.get('task_version')) is not int or snapshot['task_version']<0 or
            type(snapshot.get('next_cursor')) is not int or snapshot['next_cursor']<0 or
            not isinstance(snapshot.get('state'),str) or not snapshot['state'] or
            snapshot.get('operation_id') is not None and not isinstance(snapshot.get('operation_id'),str) or
            snapshot.get('service_id') is not None and not isinstance(snapshot.get('service_id'),str)):
        raise ProtocolError('UNKNOWN_OUTCOME: offline state snapshot invalid')
    tail=_journal_tail(base/'task.jsonl',task_id)
    if tail is None or tail['seq']<snapshot['next_cursor']:
        raise ProtocolError('UNKNOWN_OUTCOME: offline snapshot not backed by journal')
    response=envelope(task_id,snapshot['next_cursor'])
    response.update(task_version=snapshot['task_version'],operation_id=snapshot.get('operation_id'),
                    task_state=snapshot['state'],service_id=snapshot.get('service_id'),
                    result_index_ref='results/operation-index.json',
                    offline=True,source='state.json',stale=tail['seq']>snapshot['next_cursor'],
                    runtime='STOPPED' if _runtime_stopped(base) else 'UNOBSERVED')
    if response['stale']:
        response.update(journal_next_cursor=tail['seq'],
                        last_event_type=tail['type'],last_event_status=tail['status'])
        if tail['type'] in TERMINALS:
            response.update(task_state=tail['status'],operation_id=None)
    if len(encode(response))>8192:
        raise ProtocolError('TRANSPORT_PROTOCOL_ERROR: offline status exceeds budget')
    return response


def relay(root,request,*,socket_factory=socket.socket):
    if not isinstance(request,dict):
        raise ProtocolError('INVALID_INPUT')
    if request.get('kind') in ('deploy','eval','release','cancel'):
        validate_command(request)
    else:
        validate_query(request)
    limit=request.get('max_bytes',8192) if request['kind']=='read-result' else 8192
    if not hasattr(socket,'AF_UNIX'):
        raise ProtocolError('SSH_UNAVAILABLE: Unix sockets unavailable on runtime host')
    try:
        with socket_factory(socket.AF_UNIX,socket.SOCK_STREAM) as client:
            client.settimeout(request.get('wait_ms',30000)/1000+5)
            with socket_address(root) as address:
                client.connect(address)
            client.sendall(encode(request)+b'\n')
            with client.makefile('rb') as reader:
                raw=reader.readline(limit+2)
    except OSError as error:
        if request['kind'] not in ('read-result','get-status'):
            raise ProtocolError('UNKNOWN_OUTCOME: runtime socket observation failed') from error
        _offline_identity(root,request['task_id'])
        if request['kind']=='get-status':
            return _offline_status(root,request['task_id'])
        from .artifacts import read_result
        fields={key:value for key,value in request.items() if key not in ('kind','protocol_version','task_id')}
        return read_result(root,request['task_id'],**fields)
    if not raw.endswith(b'\n') or len(raw)>limit+1:
        raise ProtocolError('TRANSPORT_PROTOCOL_ERROR: runtime response framing or budget')
    try:
        response=json.loads(raw,object_pairs_hook=unique_object,parse_constant=reject_constant)
        validate_response(response,limit=limit)
        if (response['protocol_version']!='1.0' or response['task_id']!=request['task_id'] or
                response['status'] not in ('EVENTS','RUNNING','WAIT_TIMEOUT','ERROR') or
                len(encode(response))>limit):
            raise ValueError('runtime response identity')
    except (ValueError,TypeError,KeyError) as error:
        raise ProtocolError('TRANSPORT_PROTOCOL_ERROR: invalid runtime response') from error
    return response


def main():
    task=None
    request={}
    class Parser(argparse.ArgumentParser):
        def error(self,message):
            raise ProtocolError('INVALID_INPUT: client arguments')
    try:
        parser=Parser()
        parser.add_argument('--root',required=True)
        args=parser.parse_args()
        data=sys.stdin.buffer.readline(65537)
        if len(data)>65536 or not data.endswith(b'\n'):
            raise ProtocolError('INVALID_INPUT: client input framing or budget')
        request=json.loads(data,object_pairs_hook=unique_object,parse_constant=reject_constant)
        if isinstance(request,dict) and isinstance(request.get('task_id'),str) and re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}',request['task_id']):
            task=request['task_id']
        response=relay(args.root,request)
        code=exit_code(response)
    except (ValueError,KeyError,TypeError,OSError) as error:
        reason=str(error).split(':',1)[0] if isinstance(error,ProtocolError) else 'INVALID_INPUT'
        cursor=request.get('after_seq',0) if isinstance(request,dict) else 0
        if type(cursor) is not int or not 0<=cursor<2**63:
            cursor=0
        response=envelope(task,cursor,'ERROR')
        response['error']=dict(code=reason,message=reason,retryable=False)
        if isinstance(request,dict):
            for key in ('request_id','operation_id'):
                value=request.get(key)
                if isinstance(value,str) and len(value)<=64:
                    response[key]=value
        code=exit_code(response)
    sys.stdout.buffer.write(encode(response)+b'\n')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
