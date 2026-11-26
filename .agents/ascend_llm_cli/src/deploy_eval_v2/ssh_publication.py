"""Control-side streaming upload; no unlimited capture or archive base64 copy.

A self container target publishes into the same filesystem the control process
runs in, so its archives are copied locally and its receiver runs as a plain
subprocess. Every digest check is identical to the SSH path.
"""
import os
import re
import shutil
import tempfile
from pathlib import Path,PurePosixPath
from .publication import hash_file
from .protocol import ProtocolError
from .ssh_transport import container_argv,host_argv,run_remote,ssh_argv,exchange

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


def _argv_for(host, python, argv, link, container):
    if link == 'container':
        return container_argv(host, container, argv)
    return host_argv(host, argv, link)


def verify_container(host,cid,image,task_id,*,transport=run_remote,timeout=30):
    """Control-side, once before any create/publish: ID, image and running state.

    The host only needs sshd and the docker CLI; no host interpreter runs.
    """
    if not isinstance(cid,str) or not re.fullmatch('[a-f0-9]{64}',cid):
        raise ProtocolError('INVALID_INPUT: container identity')
    template='{{.Id}}|{{.Image}}|{{.State.Running}}'
    code,output,error=transport(ssh_argv(host,['docker','inspect','--format',template,cid]),
                                timeout=timeout)
    if code:
        raise ProtocolError('INVALID_INPUT: existing container unavailable: '+
                            error.decode(errors='replace')[:256])
    parts=output.decode('ascii','replace').strip().split('|')
    if (len(parts)!=3 or parts[0]!=cid or parts[2]!='true' or
            (isinstance(image,str) and image and parts[1]!=image)):
        raise ProtocolError('INVALID_INPUT: existing container identity or running state')
    return dict(Id=parts[0],Image=parts[1],Running=True)


def probe_guard_root(host,python,path,task_id,*,transport=exchange,timeout=30,link='ssh',container=None):
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
    argv=_argv_for(host,python,[python,'-B','-c',GUARD_PROBE,path],link,container)
    response=transport(argv,request,timeout=timeout,container=link=='container')
    result=response.get('result') if isinstance(response.get('result'),dict) else {}
    if response.get('status')!='EVENTS' or result.get('writable') is not True:
        raise ProtocolError('GUARD_ROOT_UNAVAILABLE: guard root not writable: '+str(result.get('error',''))[:256])
    return dict(host=host,path=path,writable=True)


def upload_archive(host,python,root,archive,task_id,*,timeout=120,transport=exchange,link='ssh',container=None):
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
    argv=_argv_for(host,python,[python,'-B','-c',source,root],link,container)
    response=transport(argv,request,input_file=archive,timeout=timeout,container=link=='container')
    if (response.get('uploaded_sha256')!=checksum or response.get('size')!=request['size'] or
            response.get('status')!='EVENTS'):
        raise ProtocolError('UNKNOWN_OUTCOME: upload identity not verified')
    return dict(host=host,path=root+'/.incoming/'+checksum+'.zip',sha256=checksum,size=request['size'])


def publish_task(host,python,root,runtime_archive,input_archive,task_id,runtime_digest,bundle_digest,*,transport=exchange,link='ssh',container=None):
    runtime=upload_archive(host,python,root,runtime_archive,task_id,transport=transport,link=link,container=container)
    inputs=upload_archive(host,python,root,input_archive,task_id,transport=transport,link=link,container=container)
    source=Path(__file__).with_name('publication_receiver.py').read_text(encoding='utf-8')
    request=dict(protocol_version='1.0',task_id=task_id,runtime_archive_sha256=runtime['sha256'],
                 inputs_archive_sha256=inputs['sha256'],runtime_sha256=runtime_digest,bundle_sha256=bundle_digest)
    argv=_argv_for(host,python,[python,'-B','-c',source,root],link,container)
    result=transport(argv,request,timeout=120,container=link=='container')
    if result.get('status')!='EVENTS' or result.get('runtime',{}).get('runtime_sha256')!=runtime_digest or result.get('inputs',{}).get('bundle_sha256')!=bundle_digest:
        raise ProtocolError('RECOVERY_REQUIRED: remote publication not verified')
    return result
