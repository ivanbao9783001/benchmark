"""Default Ascend container settings; host-specific paths belong in bindings."""
# Cards are derived from the placement (npu_ids) when the container is created;
# this list only names auxiliary driver nodes. Hosts without unified-bus skip
# /dev/ummu and /dev/uburma instead of failing.
DEVICES = ['/dev/davinci_manager', '/dev/hisi_hdc', '/dev/devmm_svm',
           '/dev/ummu', '/dev/uburma']
DEFAULT_CONTAINER = {'shm_size': '500g', 'user': 'root', 'pid_mode': 'host'}


def docker_run_args(container):
    if type(container.get('privileged')) is not bool:
        raise ValueError('Explicit privileged=true/false is required; recommended Ascend value: true')
    values = {**DEFAULT_CONTAINER, **container}
    # Docker's default is a private PID namespace; older engines reject
    # an explicit --pid=private. Keep the CLI's default host mode explicit.
    pid_args = [] if values['pid_mode'] == 'private' else ['--pid='+values['pid_mode']]
    return ['-u', values['user'], '-it', '--net=host', *pid_args,
            '--shm-size='+values['shm_size'], '--privileged='+str(values['privileged']).lower()]
