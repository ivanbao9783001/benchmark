"""Short AF_UNIX addresses for deep task roots.

Linux caps ``sun_path`` at 108 bytes including the terminating NUL, so the
control socket at ``<root>/.runtime/control.sock`` becomes unreachable once the
task root is deep (85+ characters).  Nothing else is affected: PATH_MAX is
4096 and artifact references are relative to the root.

Addressing the socket through ``/proc/self/fd/<dirfd>`` keeps the socket file
at its documented location while shortening the byte string passed to
``bind()``/``connect()`` to a constant length.  The directory descriptor must
stay open until the call completes, hence the context manager.
"""
import contextlib
import os

_CONTROL = '.runtime/control.sock'
_PROC_FD = '/proc/self/fd'


def plain_address(root):
    """Original socket path, used when procfs is unavailable."""
    return os.path.join(str(root), _CONTROL)


@contextlib.contextmanager
def socket_address(root):
    """Yield a short address for ``<root>/.runtime/control.sock``.

    The yielded string is only valid inside the ``with`` block: it may name a
    descriptor that is closed on exit.
    """
    if os.name != 'posix' or not os.path.isdir(_PROC_FD):
        yield plain_address(root)
        return
    try:
        descriptor = os.open(str(root), os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    except OSError:
        yield plain_address(root)
        return
    try:
        yield f'{_PROC_FD}/{descriptor}/{_CONTROL}'
    finally:
        os.close(descriptor)
