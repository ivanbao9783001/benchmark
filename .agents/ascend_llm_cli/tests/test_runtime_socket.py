"""Deep task roots must still reach the runtime control socket."""
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deploy_eval_v2.runtime_socket import plain_address, socket_address


@unittest.skipUnless(os.name == 'posix' and hasattr(socket, 'AF_UNIX') and
                     os.path.isdir('/proc/self/fd'), 'requires Linux Unix sockets and procfs')
class RuntimeSocketAddressTests(unittest.TestCase):
    def deep_root(self, folder):
        root = Path(folder)
        while len(str(root)) < 130:
            root = root/'segment10x'
        return root

    def test_deep_root_keeps_address_short(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.deep_root(folder)
            (root/'.runtime').mkdir(parents=True)
            self.assertGreater(len(plain_address(root)), 107)
            with socket_address(root) as address:
                self.assertLess(len(address), 107)
                self.assertTrue(address.startswith('/proc/self/fd/'))

    def test_bind_and_connect_through_procfs_alias(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.deep_root(folder)
            (root/'.runtime').mkdir(parents=True)
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                with socket_address(root) as address:
                    server.bind(address)
                    server.listen(1)
                with socket_address(root) as address:
                    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    try:
                        client.connect(address)
                        connection, _ = server.accept()
                        try:
                            client.sendall(b'ok')
                            self.assertEqual(connection.recv(2), b'ok')
                        finally:
                            connection.close()
                    finally:
                        client.close()
                self.assertTrue((root/'.runtime/control.sock').is_socket())
            finally:
                server.close()

    def test_plain_path_when_procfs_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch('deploy_eval_v2.runtime_socket.os.path.isdir', return_value=False):
                with socket_address(folder) as address:
                    self.assertEqual(address, os.path.join(str(folder), '.runtime/control.sock'))
