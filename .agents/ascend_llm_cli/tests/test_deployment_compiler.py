import copy
import json
from pathlib import Path
import unittest
from deploy_eval_v2.deployment_compiler import compile_bound_deployment
from deploy_eval_v2.json_io import load_json
from deploy_eval_v2.protocol import ProtocolError
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

    def test_pd_models_bind_all_interfaces_proxy_keeps_node_ip(self):
        result = compile_bound_deployment(deploy_plan(), bindings())
        commands = {}
        for group in result['groups'].values():
            commands.update({command['name']: command for command in group['commands']})
        for name in ('prefill', 'decode'):
            argv = commands[name]['argv']
            self.assertEqual(argv[argv.index('--host')+1], '0.0.0.0')
        proxy = commands['proxy']['argv']
        self.assertEqual(proxy[proxy.index('--host')+1], '192.0.2.1')
        self.assertEqual(proxy[proxy.index('--port')+1], '18056')
        self.assertEqual(proxy[proxy.index('--prefiller-hosts')+1], '192.0.2.1')
        self.assertEqual(proxy[proxy.index('--decoder-hosts')+1], '192.0.2.1')
        self.assertEqual(result['endpoint'], 'http://192.0.2.1:18056')
        self.assertEqual(commands['prefill']['startup_health_urls'], [])
        self.assertEqual(len(commands['proxy']['startup_health_urls']), 2)
        for health in commands['proxy']['startup_health_urls']:
            self.assertTrue(health.startswith('http://192.0.2.1:'))
