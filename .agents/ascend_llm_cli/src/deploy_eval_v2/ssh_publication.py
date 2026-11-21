"""Control-side streaming upload; no unlimited capture or archive base64 copy.

A self container target publishes into the same filesystem the control process
runs in, so its archives are copied locally and its receiver runs as a plain
subprocess. Every digest check is identical to the SSH path.
"""
import os
import shutil
import tempfile
from pathlib import Path,PurePosixPath
from .publication import hash_file
from .protocol import ProtocolError
from .ssh_transport import host_argv,exchange

GUARD_PROBE='''
import json,os,sys,tempfile
request=json.loads(sys.stdin.readline())
path=sys.argv[1]
try:
    os.makedirs(path,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path):
        pass
    result={'writable':True,'path':path}
except OSError as error:
    result={'writable':False,'path':path,'error':str(error)[:512]}
print(json.dumps({'protocol_version':'1.0','task_id':request['task_id'],'status':'EVENTS','result':result}))
'''


def _validate_target(host,python,path):
    if (not isinstance(path,str) or not path.startswith('/') or path=='/' or
            len(path)>4096 or '..' in PurePosixPath(path).parts or any(c in path for c in '\x00\r\n\\') or
            not isinstance(python,str) or not python.startswith('/')):
        raise ProtocolError('INVALID_INPUT: publication target')


def probe_guard_root(host,python,path,task_id,*,transport=exchange,timeout=30,link='ssh'):
    """Fail before upload when the host-global guard root is not writable."""
    _validate_target(host,python,path)
    if link=='local':
        try:
            os.makedirs(path,exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path):
                pass
        except OSError as error:
            raise ProtocolError('GUARD_ROOT_UNAVAILABLE: guard root not writable: '+str(error)[:256]) from error
        return dict(host=host,path=path,writable=True)
    request=dict(protocol_version='1.0',task_id=task_id)
    response=transport(host_argv(host,[python,'-B','-c',GUARD_PROBE,path]),request,timeout=timeout)
    result=response.get('result') if isinstance(response.get('result'),dict) else {}
    if response.get('status')!='EVENTS' or result.get('writable') is not True:
        raise ProtocolError('GUARD_ROOT_UNAVAILABLE: guard root not writable: '+str(result.get('error',''))[:256])
    return dict(host=host,path=path,writable=True)


def upload_archive(host,python,root,archive,task_id,*,timeout=120,transport=exchange,link='ssh'):
    _validate_target(host,python,root)
    archive=Path(archive)
    if archive.is_symlink() or not 0<archive.stat().st_size<=64*1024*1024:
        raise ProtocolError('INVALID_INPUT: upload archive')
    checksum=hash_file(archive)
    source=Path(__file__).with_name('blob_receiver.py').read_text(encoding='utf-8')
    request=dict(protocol_version='1.0',task_id=task_id,sha256=checksum,size=archive.stat().st_size)
    if link=='local':
        incoming=Path(root)/'.incoming'
        incoming.mkdir(parents=True,exist_ok=True)
        destination=incoming/(checksum+'.zip')
        if destination.exists():
            if destination.is_symlink() or hash_file(destination)!=checksum:
                raise ProtocolError('REQUEST_CONFLICT: local upload identity')
        else:
            shutil.copyfile(archive,destination)
        if hash_file(destination)!=checksum:
            raise ProtocolError('UNKNOWN_OUTCOME: upload identity not verified')
        return dict(host=host,path=str(destination),sha256=checksum,size=request['size'])
    response=transport(host_argv(host,[python,'-B','-c',source,root]),request,
                       input_file=archive,timeout=timeout)
    if (response.get('uploaded_sha256')!=checksum or response.get('size')!=request['size'] or
            response.get('status')!='EVENTS'):
        raise ProtocolError('UNKNOWN_OUTCOME: upload identity not verified')
    return dict(host=host,path=root+'/.incoming/'+checksum+'.zip',sha256=checksum,size=request['size'])


def publish_task(host,python,root,runtime_archive,input_archive,task_id,runtime_digest,bundle_digest,*,transport=exchange,link='ssh'):
    runtime=upload_archive(host,python,root,runtime_archive,task_id,transport=transport,link=link)
    inputs=upload_archive(host,python,root,input_archive,task_id,transport=transport,link=link)
    source=Path(__file__).with_name('publication_receiver.py').read_text(encoding='utf-8')
    request=dict(protocol_version='1.0',task_id=task_id,runtime_archive_sha256=runtime['sha256'],
                 inputs_archive_sha256=inputs['sha256'],runtime_sha256=runtime_digest,bundle_sha256=bundle_digest)
    result=transport(host_argv(host,[python,'-B','-c',source,root],link),request,timeout=120)
    if result.get('status')!='EVENTS' or result.get('runtime',{}).get('runtime_sha256')!=runtime_digest or result.get('inputs',{}).get('bundle_sha256')!=bundle_digest:
        raise ProtocolError('RECOVERY_REQUIRED: remote publication not verified')
    return result
