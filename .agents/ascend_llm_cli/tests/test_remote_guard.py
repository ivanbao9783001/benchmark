from pathlib import Path
import tempfile
import unittest
from deploy_eval_v2.remote_guard import HostGuard, require_owned_container, exclusive
from deploy_eval_v2.protocol import ProtocolError
from deploy_eval_v2.compiler import digest
from deploy_eval_v2.durable import atomic_json


class RemoteGuardTests(unittest.TestCase):
    def test_holds_reports_only_own_live_claims(self):
        with tempfile.TemporaryDirectory() as folder:
            guard=HostGuard(folder,'t','a'*64)
            guard.claim(['npu:5'])
            self.assertTrue(guard.holds(['npu:5','npu:6']))
            self.assertFalse(guard.holds(['npu:6']))
            guard.release_claims(['npu:5'],resources_verified_absent=True)
            self.assertFalse(guard.holds(['npu:5']))

    def test_precreate_cleanup_uses_durable_effect_marker(self):
        with tempfile.TemporaryDirectory() as folder:
            guard=HostGuard(folder,'t','a'*64)
            guard.claim(['npu:0'])
            self.assertTrue(guard.release_unstarted(['npu:0'])['claims_released'])
            guard.claim(['npu:0'])
            def remapped():
                raise ProtocolError('RECOVERY_REQUIRED: container devices remapped')
            with self.assertRaises(ProtocolError):
                guard.command('create1',{'kind':'create'},remapped)
            # Read-only preflight rejected before any side effect: safe to unlock.
            self.assertFalse(guard.effect_started('create1'))
            self.assertTrue(guard.release_unstarted(['npu:0'])['claims_released'])
            guard.claim(['npu:0'])
            def started_then_lost():
                guard.mark_effect_started('create2')
                raise OSError('create outcome lost')
            with self.assertRaises(OSError):
                guard.command('create2',{'kind':'create'},started_then_lost)
            # A recorded side effect keeps the host locked for inventory review.
            with self.assertRaisesRegex(ProtocolError,'prior resource effect'):
                guard.release_unstarted(['npu:0'])
            with self.assertRaisesRegex(ProtocolError,'RESOURCE_BUSY'):
                HostGuard(folder,'other','b'*64).claim(['npu:0'])

    def test_legacy_create_record_without_marker_stays_locked(self):
        with tempfile.TemporaryDirectory() as folder:
            guard=HostGuard(folder,'t','a'*64)
            guard.claim(['npu:0'])
            key=digest(dict(owner=guard.owner,command_id='old'))
            atomic_json(guard.root/'commands'/(key+'.json'),
                        dict(owner=guard.owner,command_id='old',payload_sha256='x',
                             effect_kind='create',status='ACCEPTED'))
            with self.assertRaisesRegex(ProtocolError,'prior resource effect'):
                guard.release_unstarted(['npu:0'])

    def test_cross_task_claims_survive_reconstruction(self):
        with tempfile.TemporaryDirectory() as folder:
            first = HostGuard(folder, 'a', 'a'*64)
            first.claim(['npu:0', 'tcp:18000'])
            HostGuard(folder, 'a', 'a'*64).claim(['npu:0'])
            second = HostGuard(folder, 'b', 'b'*64)
            with self.assertRaisesRegex(ProtocolError, 'RESOURCE_BUSY'):
                second.claim(['npu:0'])
            with self.assertRaises(ProtocolError):
                first.release_claims(['npu:0'], resources_verified_absent=False)
            first.release_claims(['npu:0'], resources_verified_absent=True)
            second.claim(['npu:0'])

    def test_command_loss_never_reexecutes(self):
        with tempfile.TemporaryDirectory() as folder:
            guard = HostGuard(folder, 'a', 'a'*64)
            calls = []
            def action():
                calls.append(1)
                return {'container_id': 'abc'}
            self.assertEqual(guard.command('create', {'image': 'x'}, action), {'container_id': 'abc'})
            self.assertEqual(guard.command('create', {'image': 'x'}, action), {'container_id': 'abc'})
            self.assertEqual(len(calls), 1)
            with self.assertRaisesRegex(ProtocolError, 'REQUEST_CONFLICT'):
                guard.command('create', {'image': 'y'}, action)
            def crash():
                raise OSError('lost after side effect')
            with self.assertRaises(OSError):
                guard.command('unknown', {}, crash)
            with self.assertRaisesRegex(ProtocolError, 'RECOVERY_REQUIRED'):
                guard.command('unknown', {}, action)
            self.assertEqual(len(calls), 1)

    def test_exclusive_lock_and_ownership(self):
        with tempfile.TemporaryDirectory() as folder:
            with exclusive(Path(folder)/'lock'):
                with self.assertRaises(ProtocolError):
                    with exclusive(Path(folder)/'lock'):
                        self.fail('must not acquire')
        inspect = {'Id': 'id', 'Config': {'Labels': {'bench.task': 't', 'bench.execution': 'token'}}}
        require_owned_container(inspect, 't', 'token', 'id')
        for task, token, cid in [('other', 'token', 'id'), ('t', 'old', 'id'), ('t', 'token', 'reused-name')]:
            with self.assertRaises(ProtocolError):
                require_owned_container(inspect, task, token, cid)

    def test_foreign_claim_does_not_partially_unlock_batch(self):
        with tempfile.TemporaryDirectory() as folder:
            first = HostGuard(folder, 'a', 'a'*64)
            second = HostGuard(folder, 'b', 'b'*64)
            first.claim(['npu:0'])
            second.claim(['npu:1'])
            with self.assertRaises(ProtocolError):
                first.release_claims(['npu:0', 'npu:1'], resources_verified_absent=True)
            with self.assertRaisesRegex(ProtocolError, 'RESOURCE_BUSY'):
                second.claim(['npu:0'])
