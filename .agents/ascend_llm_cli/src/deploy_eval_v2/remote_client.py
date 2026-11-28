"""Disposable SSH-side Unix socket client; never runs stage work."""
import argparse
import json
import re
import socket
import sys
from .protocol import encode, envelope,ProtocolError
from .json_io import unique_object,reject_constant
from .contracts import validate_command,validate_query,exit_code
from .wire_contracts import validate_response
from .runtime_socket import socket_address
from .task_snapshot import offline_identity as _offline_identity,offline_status as _offline_status


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
    schema_error = None
    try:
        response=json.loads(raw,object_pairs_hook=unique_object,parse_constant=reject_constant)
        try:
            validate_response(response,limit=limit)
        except ImportError as error:
            if request['kind']=='get-status':
                _offline_identity(root,request['task_id'])
                return _offline_status(root,request['task_id'])
            schema_error = error
        if (response['protocol_version']!='1.0' or response['task_id']!=request['task_id'] or
                response['status'] not in ('EVENTS','RUNNING','WAIT_TIMEOUT','ERROR') or
                len(encode(response))>limit):
            raise ValueError('runtime response identity')
    except (ValueError,TypeError,KeyError) as error:
        if schema_error is not None:
            raise ProtocolError('OBSERVER_UNAVAILABLE: runtime response schema unavailable') from schema_error
        raise ProtocolError('TRANSPORT_PROTOCOL_ERROR: invalid runtime response') from error
    if schema_error is not None:
        raise ProtocolError('OBSERVER_UNAVAILABLE: runtime response schema unavailable') from schema_error
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
        detail=str(error)[:300] if reason=='OBSERVER_UNAVAILABLE' else reason
        response['error']=dict(code=reason,message=detail,retryable=False)
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
