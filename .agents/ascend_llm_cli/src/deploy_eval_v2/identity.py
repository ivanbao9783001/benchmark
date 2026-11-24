"""Machine identity shared by readiness, executor selection and the CLI.

The preferred identity is the 32-hex ``/etc/machine-id``. Containers built from
images that never ran systemd boot leave that file empty, so the fallback derives
a stable identity from the hostname instead of demanding manual preparation.
"""
import hashlib
import re
import socket
from pathlib import Path
from .protocol import ProtocolError

MACHINE_ID_PATH = Path('/etc/machine-id')
DERIVATION_PREFIX = 'deploy-eval-v2:v1:'


def read_machine_id():
    """The 32-hex machine id, or None when the file is absent, empty or malformed."""
    try:
        value = MACHINE_ID_PATH.read_text(encoding='ascii').strip()
    except (OSError, UnicodeError):
        return None
    return value if re.fullmatch('[a-f0-9]{32}', value) else None


def derive_hostname_id(hostname):
    """A stable 32-hex identity for hosts that expose no machine id."""
    return hashlib.sha256((DERIVATION_PREFIX+hostname).encode('utf-8')).hexdigest()[:32]


def resolve_identity(hostname_reader=None):
    """Return ``{'machine_id': ..., 'source': 'machine-id'|'hostname-derived'}``."""
    value = read_machine_id()
    if value is not None:
        return dict(machine_id=value, source='machine-id')
    hostname = (hostname_reader or socket.gethostname)()
    hostname = hostname.strip() if isinstance(hostname, str) else ''
    if not hostname:
        raise ProtocolError('INVALID_INPUT: machine identity unavailable: '
                            '/etc/machine-id is empty and the hostname is unknown')
    return dict(machine_id=derive_hostname_id(hostname), source='hostname-derived')
