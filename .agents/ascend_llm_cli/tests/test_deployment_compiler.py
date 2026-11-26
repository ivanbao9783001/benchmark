import copy
import json
from pathlib import Path
import tempfile
import unittest
from deploy_eval_v2.compiler import compiled_deployment, freeze_inputs, review_inputs, verify_frozen
from deploy_eval_v2.deployment_compiler import compile_bound_deployment
from deploy_eval_v2.json_io import load_json
from deploy_eval_v2.protocol import ProtocolError
from deploy_eval_v2.topology import producer_identity_warnings
from test_deployment_model import deploy_plan


def bindings():
    value = load_json(Path(__file__).parent/'fixtures/deploy-bindings.json')
    value.update(schema_version='1.0', task_id='t')
    return value


class DeploymentCompilerTests(unittest.TestCase):
    def test_complete_reference_compiles_without_mutation(self):
        plan, bound = deploy_plan(), bindings()
        original = copy.deepcopy((plan, bound))
        result = compile_bound_deployment(plan, bound)
        self.assertEqual((plan, bound), original)
        self.assertEqual(result['endpoint'], 'http://192.0.2.1:18056')
        commands = result['groups']['node_a/model']['commands']
        model = commands[0]
        self.assertIn('/path/to/model-weights', model['argv'])
        self.assertIn('unset HCCL_INTRA_ROCE_ENABLE', model['bootstrap'])
        kv = json.loads(model['argv'][model['argv'].index('--kv-transfer-config')+1])
        self.assertEqual(kv['kv_connector'], 'MooncakeHybridConnector')
        self.assertEqual(len(commands[-1]['startup_health_urls']), 2)

    def test_conflicting_ports_devices_and_environment(self):
        for kind in ('port', 'device', 'env', 'missing'):
            plan, bound = deploy_plan(), bindings()
            if kind == 'port':
                bound['nodes']['node_a']['deployments']['pd-a']['ports']['decode']['http'] = 18000
            elif kind == 'device':
                plan['deployment']['instances'][1]['placement']['devices'][0] = 0
            elif kind == 'env':
                plan['deployment']['instances'][0]['launch']['env']['HCCL_IF_IP'] = '192.0.2.99'
            else:
                del bound['nodes']['node_a']['paths']['proxy_program']
            with self.assertRaises(ProtocolError):
                compile_bound_deployment(plan, bound)

    def test_kv_synthesis_is_connector_neutral(self):
        for connector in ('MooncakeHybridConnector', 'MooncakeConnectorV2', 'OtherConnector'):
            plan, bound = deploy_plan(), bindings()
            plan['deployment']['connections']['kv_transfer'][0]['connector'] = connector
            result = compile_bound_deployment(plan, bound)
            commands = {}
            for group in result['groups'].values():
                commands.update({command['name']: command for command in group['commands']})
            argv = commands['prefill']['argv']
            kv = json.loads(argv[argv.index('--kv-transfer-config')+1])
            self.assertEqual(kv['kv_connector'], connector)
            self.assertNotIn('ascend_local_comm_res_path', kv['kv_connector_extra_config'])

    def test_engine_id_duplicate_warning_is_connector_neutral(self):
        dep = {'id': 't', 'instances': [
            {'id': 'p1', 'role': 'prefill', 'launch': {'engine_id': 'same'}},
            {'id': 'p2', 'role': 'prefill', 'launch': {'engine_id': 'same'}},
            {'id': 'd1', 'role': 'decode', 'launch': {}}],
            'connections': {'kv_transfer': [
                {'producer': 'p1', 'consumer': 'd1', 'connector': 'AnyConnector'},
                {'producer': 'p2', 'consumer': 'd1', 'connector': 'AnyConnector'}]}}
        warnings = producer_identity_warnings(dep)
        self.assertEqual(len(warnings), 1)
        self.assertIn('AnyConnector', warnings[0])
        self.assertIn('UNVERIFIED', warnings[0])

    def test_schema_error_names_the_field_and_port_shape(self):
        plan, bound = deploy_plan(), bindings()
        bound['nodes']['node_a']['deployments']['pd-a']['ports'] = {'http': 18000, 'hccl': 18009}
        with self.assertRaisesRegex(ProtocolError,
                                    r"deployments\['pd-a'\]\.ports.*instance id"):
            compile_bound_deployment(plan, bound)


def colocated_plan():
    plan = deploy_plan()
    dep = plan['deployment']
    server = copy.deepcopy(dep['instances'][0])
    server['id'] = 'srv'
    server['role'] = 'mixed'
    server['placement']['devices'] = [0, 1, 2, 3]
    server['launch']['args'] = {'data-parallel-size': 1, 'tensor-parallel-size': 4,
                                'max-model-len': 8192}
    server['launch']['env'] = {'VLLM_HOST_IP': '{{network.ip}}', 'HCCL_IF_IP': '{{network.ip}}',
                               'GLOO_SOCKET_IFNAME': '{{network.interface}}',
                               'TP_SOCKET_IFNAME': '{{network.interface}}',
                               'HCCL_SOCKET_IFNAME': '{{network.interface}}',
                               'HCCL_IF_BASE_PORT': '{{ports.hccl}}'}
    for key in ('preserve_reference', 'engine_id', 'kv_transfer_config'):
        server['launch'].pop(key, None)
    server['ports'] = {'http': 1, 'rpc': 4, 'hccl': 16}
    dep.update(id='c-a', topology='colocated', instances=[server],
               connections={}, endpoint={'instance': 'srv', 'port': 'http'})
    return plan


def colocated_bindings():
    value = bindings()
    node = value['nodes']['node_a']
    node['deployments'] = {'c-a': {
        'network': {'ip': '192.0.2.1', 'interface': 'eth0'},
        'ports': {'srv': {'http': 18000, 'rpc': 18004, 'hccl': 18009}}}}
    for key in ('container', 'container_work_root'):
        node.pop(key, None)
    return value


class ListenAddressTests(unittest.TestCase):
    """Model API servers listen on all interfaces; endpoints keep the bound IP."""

    def test_colocated_serves_loopback_and_node_ip(self):
        result = compile_bound_deployment(colocated_plan(), colocated_bindings())
        command = result['groups']['node_a/model']['commands'][0]
        argv = command['argv']
        self.assertEqual(argv.count('--host'), 1)
        self.assertEqual(argv[argv.index('--host')+1], '0.0.0.0')
        self.assertEqual(argv[argv.index('--port')+1], '18000')
        self.assertEqual(argv[argv.index('--data-parallel-address')+1], '192.0.2.1')
        self.assertEqual(command['env']['VLLM_HOST_IP'], '192.0.2.1')
        self.assertEqual(command['env']['HCCL_IF_IP'], '192.0.2.1')
        self.assertEqual(result['endpoint'], 'http://192.0.2.1:18000')
        self.assertEqual(result['groups']['node_a/model']['health_urls'],
                         ['http://192.0.2.1:18000/health'])
        self.assertEqual(result['metrics_urls'], ['http://192.0.2.1:18000/metrics'])
        self.assertEqual(result['reset_urls'], ['http://192.0.2.1:18000/reset_prefix_cache'])

    def test_pd_models_and_proxy_bind_all_interfaces(self):
        result = compile_bound_deployment(deploy_plan(), bindings())
        commands = {}
        for group in result['groups'].values():
            commands.update({command['name']: command for command in group['commands']})
        for name in ('prefill', 'decode', 'proxy'):
            argv = commands[name]['argv']
            self.assertEqual(argv[argv.index('--host')+1], '0.0.0.0')
        proxy = commands['proxy']['argv']
        self.assertEqual(proxy[proxy.index('--port')+1], '18056')
        self.assertEqual(proxy[proxy.index('--prefiller-hosts')+1], '192.0.2.1')
        self.assertEqual(proxy[proxy.index('--decoder-hosts')+1], '192.0.2.1')
        self.assertEqual(result['endpoint'], 'http://192.0.2.1:18056')
        self.assertEqual(commands['prefill']['startup_health_urls'], [])
        self.assertEqual(len(commands['proxy']['startup_health_urls']), 2)
        for health in commands['proxy']['startup_health_urls']:
            self.assertTrue(health.startswith('http://192.0.2.1:'))

    def test_pd_engine_id_is_derived_when_absent(self):
        plan = deploy_plan()
        for item in plan['deployment']['instances']:
            item['launch'].pop('engine_id', None)
        result = compile_bound_deployment(plan, bindings())
        argv = next(command['argv'] for group in result['groups'].values()
                    for command in group['commands'] if command['name'] == 'prefill')
        kv_text = next(str(value) for value in argv if 'engine_id' in str(value))
        self.assertIn('pd-a-prefill', kv_text)


def borrowed_colocated(devices, mapping=None):
    """Single borrowed node whose mixed instance is placed on physical devices."""
    plan = colocated_plan()
    server = plan['deployment']['instances'][0]
    server['placement']['devices'] = list(devices)
    server['launch']['args']['tensor-parallel-size'] = len(devices)
    plan['deployment']['runtime'].clear()
    plan['deployment']['runtime']['mode'] = 'existing_container'
    bound = colocated_bindings()
    node = bound['nodes']['node_a']
    node['container'] = dict(id='c'*64, image='sha256:'+'d'*64)
    node['container_work_root'] = '/workspace/tasks'
    if mapping is not None:
        node['container']['device_id_map'] = mapping
    return plan, bound


class ContainerDeviceIdMapTests(unittest.TestCase):
    """A borrowed container may expose selected physical NPUs under other logical IDs."""

    def test_declared_map_selects_logical_ids_and_keeps_physical_claims(self):
        plan, bound = borrowed_colocated([5, 6], {'5': 0, '6': 1})
        group = compile_bound_deployment(plan, bound)['groups']['node_a/model']
        self.assertEqual(group['npu_ids'], [5, 6])
        self.assertEqual(group['device_id_map'], {'5': 0, '6': 1})
        command = group['commands'][0]
        self.assertEqual(command['env']['ASCEND_RT_VISIBLE_DEVICES'], '0,1')
        self.assertNotIn('device-ids', command['argv'])

    def test_absent_map_keeps_physical_selection(self):
        plan, bound = borrowed_colocated([5, 6])
        group = compile_bound_deployment(plan, bound)['groups']['node_a/model']
        self.assertNotIn('device_id_map', group)
        self.assertEqual(group['commands'][0]['env']['ASCEND_RT_VISIBLE_DEVICES'], '5,6')

    def test_map_must_cover_physical_placement_exactly(self):
        for mapping in ({'5': 0}, {'5': 0, '6': 1, '7': 2},
                        {'5': 0, '6': 0}, {'5': 0, '6': 2}):
            with self.subTest(mapping=mapping):
                plan, bound = borrowed_colocated([5, 6], mapping)
                with self.assertRaises(ProtocolError):
                    compile_bound_deployment(plan, bound)

    def test_remapped_plan_device_syntax_is_rejected(self):
        plan, bound = borrowed_colocated([5, 6], {'5:0': 0, '6': 1})
        with self.assertRaises(ProtocolError):
            compile_bound_deployment(plan, bound)

    def test_device_map_basis_is_recorded_for_every_mode(self):
        group = compile_bound_deployment(deploy_plan(), bindings())['groups']['node_a/model']
        self.assertEqual(group['device_map'], {'declared': None, 'basis': 'physical'})
        plan, bound = borrowed_colocated([5, 6])
        group = compile_bound_deployment(plan, bound)['groups']['node_a/model']
        self.assertEqual(group['device_map'], {'declared': None, 'basis': 'identity_assumed'})
        mapping = {'5': 0, '6': 1}
        plan, bound = borrowed_colocated([5, 6], mapping)
        group = compile_bound_deployment(plan, bound)['groups']['node_a/model']
        self.assertEqual(group['device_map'], {'declared': mapping, 'basis': 'declared'})
        group['device_map']['declared']['5'] = 9
        self.assertEqual(bound['nodes']['node_a']['container']['device_id_map']['5'], 0)

    def test_owned_mode_cannot_declare_a_map(self):
        plan, bound = borrowed_colocated([5, 6], {'5': 0, '6': 1})
        plan['deployment']['runtime'].clear()
        plan['deployment']['runtime'].update(
            image='image', container=dict(shm_size='1g', privileged=False))
        with self.assertRaisesRegex(ProtocolError, 'borrowed container binding|schema'):
            compile_bound_deployment(plan, bound)

    def test_plan_runtime_cannot_carry_the_map(self):
        plan, bound = borrowed_colocated([5, 6])
        plan['deployment']['runtime']['container'] = dict(
            shm_size='1g', privileged=False, device_id_map={'5': 0, '6': 1})
        with self.assertRaises(ProtocolError):
            compile_bound_deployment(plan, bound)

    def test_explicit_selection_must_match_logical_ids(self):
        plan, bound = borrowed_colocated([5, 6], {'5': 0, '6': 1})
        plan['deployment']['instances'][0]['launch']['env']['ASCEND_RT_VISIBLE_DEVICES'] = '5,6'
        with self.assertRaisesRegex(ProtocolError, 'device selection'):
            compile_bound_deployment(plan, bound)

    def test_each_node_uses_its_own_map(self):
        plan, bound = deploy_plan(), bindings()
        bound['nodes']['node_b'] = copy.deepcopy(bound['nodes']['node_a'])
        node_a, node_b = bound['nodes']['node_a'], bound['nodes']['node_b']
        node_a['container'] = dict(id='c'*64, image='sha256:'+'d'*64,
                                   device_id_map={'0': 0, '1': 1, '2': 2, '3': 3})
        node_a['container_work_root'] = '/workspace/tasks'
        node_b['container'] = dict(id='e'*64, image='sha256:'+'f'*64,
                                   device_id_map={'4': 0, '5': 1, '6': 2, '7': 3})
        node_b['container_work_root'] = '/workspace/tasks-b'
        node_b['host'] = 'user@benchmark-host-b.example'
        node_b['work_dir'] = '/path/to/task-workspace-b'
        node_b['deployments']['pd-a']['network'] = {'ip': '192.0.2.2', 'interface': 'eth0'}
        node_b['deployments']['pd-a']['ports'] = {
            'decode': node_a['deployments']['pd-a']['ports'].pop('decode')}
        plan['deployment']['instances'][1]['placement']['node'] = 'node_b'
        plan['deployment']['runtime'].clear()
        plan['deployment']['runtime']['mode'] = 'existing_container'
        result = compile_bound_deployment(plan, bound)
        self.assertEqual(result['groups']['node_a/model']['npu_ids'], [0, 1, 2, 3])
        self.assertEqual(result['groups']['node_b/model']['npu_ids'], [4, 5, 6, 7])
        self.assertEqual(result['groups']['node_a/model']['commands'][0]['env']
                         ['ASCEND_RT_VISIBLE_DEVICES'], '0,1,2,3')
        self.assertEqual(result['groups']['node_b/model']['commands'][0]['env']
                         ['ASCEND_RT_VISIBLE_DEVICES'], '0,1,2,3')


class ModelNamingTests(unittest.TestCase):
    """served_name is optional; the frozen package echoes the id-to-served mapping."""

    def test_compiled_echoes_both_names_when_served_name_is_explicit(self):
        result = compiled_deployment(deploy_plan(), bindings())
        self.assertEqual(result['model'], 'dsv')
        self.assertEqual(result['model_id'], 'dsv4')

    def test_omitted_served_name_derives_from_id_through_freeze(self):
        plan, bound = deploy_plan(), bindings()
        del plan['deployment']['model']['served_name']
        review = dict(schema_version='1.0', status='APPROVED', unresolved=[],
                      evidence_refs=['review/deploy.json'],
                      inputs=review_inputs(plan, bound, 'a'*64))
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)/'frozen'
            reference = freeze_inputs(target, plan, bound, review, 'a'*64)
            values = verify_frozen(target, reference['sha256'], 'a'*64)
        compiled = values['compiled.json']
        self.assertEqual(compiled['model'], 'dsv4')
        self.assertEqual(compiled['model_id'], 'dsv4')
        argv = compiled['topology']['groups']['node_a/model']['commands'][0]['argv']
        self.assertEqual(argv[argv.index('--served-model-name')+1], 'dsv4')
