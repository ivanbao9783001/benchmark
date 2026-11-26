from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from deploy_eval_v2 import identity
from deploy_eval_v2.readiness import check_ready
from deploy_eval_v2.compiler import freeze_inputs,review_inputs
from deploy_eval_v2.publication import build_archive
from deploy_eval_v2.plan_model import normalize_external_plan
from test_plan_model import external_plan,eval_bindings


def device_map_values(mapping, devices=(5,6), mode='existing_container', self_node=True):
    container=dict(id='c'*64,image='sha256:'+'d'*64)
    if self_node:
        container['self']=True
    if mapping is not None:
        container['device_id_map']=mapping
    values={'plan.json':dict(task_id='t',intent='deploy',
                deployment=dict(runtime=dict(mode=mode),
                    instances=[dict(id='srv',role='mixed',
                                    placement=dict(node='a',devices=list(devices)))])),
            'bindings.json':dict(nodes={'a':dict(machine_id='a'*32,container=container)}),
            'compiled.json':{'topology':{'groups':{'a/model':dict(node='a',host='host-a',root='/work/t')}}}}
    request=dict(task_ref='t',runtime_archive='unused.zip',input_directory='unused',
                 runtime_sha256='c'*64,bundle_sha256='d'*64)
    config={'tasks':{'t':dict(host='host-a',root='/work/t',allowed_root='/work',
                              task_root='/tmp/delivery/t',self=self_node)}}
    return values,request,config


class DeviceIdMapReadinessTests(unittest.TestCase):
    """Declared mappings are statically validated and probed on the local node."""

    def readiness(self, mapping, observed=None, mode='existing_container', self_node=True):
        values,request,config=device_map_values(mapping,mode=mode,self_node=self_node)
        with patch('deploy_eval_v2.readiness.verify_frozen',return_value=values),\
             patch('deploy_eval_v2.readiness.install_archive'),\
             patch('deploy_eval_v2.readiness.shutil.which',return_value=None),\
             patch('deploy_eval_v2.readiness.control_is_posix',return_value=True),\
             patch('deploy_eval_v2.readiness.local_machine_id',return_value='a'*32),\
             patch('deploy_eval_v2.readiness.read_local_chip_logic_map',return_value=observed):
            return check_ready(request,config)['readiness']

    def test_local_chip_map_must_match_the_declared_mapping(self):
        self.assertEqual(self.readiness({'5':0,'6':1},{5:0,6:1})['status'],
                         'READY_FOR_HOST_PREFLIGHT')
        self.assertIn('DEVICE_ID_MAP_MISMATCH',
                      self.readiness({'5':0,'6':1},{5:0,6:2})['blockers'])
        self.assertIn('DEVICE_ID_MAP_UNVERIFIABLE',
                      self.readiness({'5':0,'6':1},None)['blockers'])

    def test_incomplete_or_owned_maps_are_static_blockers(self):
        self.assertIn('DEVICE_ID_MAP_INVALID',self.readiness({'5':0})['blockers'])
        self.assertIn('DEVICE_ID_MAP_INVALID',
                      self.readiness({'5':0,'6':1},mode='owned_container')['blockers'])

    def test_absent_map_keeps_the_existing_readiness_path(self):
        self.assertEqual(self.readiness(None,None)['blockers'],[])

    def test_absent_map_blocks_a_remapped_local_self_node(self):
        ready=self.readiness(None,{5:5,6:6})
        self.assertEqual(ready['status'],'READY_FOR_HOST_PREFLIGHT')
        blocked=self.readiness(None,{5:0,6:1})
        self.assertEqual(blocked['status'],'BLOCKED')
        self.assertIn('DEVICE_ID_MAP_REQUIRED',blocked['blockers'])
        self.assertEqual(blocked['remote_checks'],'NOT_RUN')
        self.assertNotIn('DEVICE_ID_MAP_REQUIRED',
                         self.readiness(None,{5:0,6:1},mode='owned_container',
                                        self_node=False)['blockers'])


class ReadinessTests(unittest.TestCase):
    def test_empty_control_machine_id_is_derived_from_the_hostname(self):
        values,request,config=device_map_values(None)
        derived=identity.derive_hostname_id('control-host')
        values['bindings.json']['nodes']['a']['machine_id']=derived
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'machine-id'
            path.write_text('',encoding='ascii')
            with patch('deploy_eval_v2.readiness.verify_frozen',return_value=values),\
                 patch('deploy_eval_v2.readiness.install_archive'),\
                 patch('deploy_eval_v2.readiness.shutil.which',return_value=None),\
                 patch('deploy_eval_v2.readiness.control_is_posix',return_value=True),\
                 patch.object(identity,'MACHINE_ID_PATH',path),\
                 patch.object(identity.socket,'gethostname',return_value='control-host'):
                result=check_ready(request,config)['readiness']
        self.assertEqual(result['status'],'READY_FOR_HOST_PREFLIGHT')
        self.assertNotIn('SELF_MACHINE_ID_UNAVAILABLE',result['blockers'])

    def test_a_foreign_hostname_derived_identity_stays_a_mismatch(self):
        values,request,config=device_map_values(None)
        with patch('deploy_eval_v2.readiness.verify_frozen',return_value=values),\
             patch('deploy_eval_v2.readiness.install_archive'),\
             patch('deploy_eval_v2.readiness.shutil.which',return_value=None),\
             patch('deploy_eval_v2.readiness.control_is_posix',return_value=True),\
             patch('deploy_eval_v2.readiness.local_machine_id',
                   return_value=identity.derive_hostname_id('other-host')):
            result=check_ready(request,config)['readiness']
        self.assertIn('SELF_MACHINE_ID_MISMATCH',result['blockers'])
        self.assertNotIn('SELF_MACHINE_ID_UNAVAILABLE',result['blockers'])

    def test_differing_control_machine_id_stays_a_mismatch(self):
        values,request,config=device_map_values(None)
        with patch('deploy_eval_v2.readiness.verify_frozen',return_value=values),\
             patch('deploy_eval_v2.readiness.install_archive'),\
             patch('deploy_eval_v2.readiness.shutil.which',return_value=None),\
             patch('deploy_eval_v2.readiness.control_is_posix',return_value=True),\
             patch('deploy_eval_v2.readiness.local_machine_id',return_value='b'*32):
            result=check_ready(request,config)['readiness']
        self.assertIn('SELF_MACHINE_ID_MISMATCH',result['blockers'])

    def test_owned_unsupported_parameters_are_reported_without_remote_actions(self):
        values={'plan.json':dict(task_id='t',intent='deploy',execution={'max_retries':1},
                    evaluation=dict(test_binding='tester',cases=[{'reset_cache':True}])),
                'bindings.json':dict(nodes={'a':{'machine_id':'a'*32}},
                    testers={'tester':{'machine_id':'b'*32,'work_root':'/test','host':'tester-host'}}),
                'compiled.json':{'topology':{'groups':{'a/model':dict(node='a',host='host-a',root='/work/t')}}}}
        request=dict(task_ref='t',runtime_archive='unused.zip',input_directory='unused',
                     runtime_sha256='c'*64,bundle_sha256='d'*64)
        config={'tasks':{'t':dict(host='host-a',root='/work/t',allowed_root='/work',task_root='/tmp/delivery/t',
                                tester=dict(host='tester-host',root='/test/t',allowed_root='/test'))}}
        with patch('deploy_eval_v2.readiness.verify_frozen',return_value=values),\
             patch('deploy_eval_v2.readiness.install_archive'),\
             patch('deploy_eval_v2.readiness.shutil.which',return_value='/ssh'):
            result=check_ready(request,config)
            self.assertEqual(result['readiness']['blockers'],[])
            values['plan.json']['execution']['max_retries']=0
            values['plan.json']['evaluation']['cases'][0]['reset_cache']=False
            values['bindings.json']['testers']['tester']['machine_id']='a'*32
            self.assertEqual(check_ready(request,config)['readiness']['status'],'READY_FOR_HOST_PREFLIGHT')

    def test_self_container_requires_one_control_node_and_local_identity(self):
        values={'plan.json':dict(task_id='t',intent='deploy',
                    deployment=dict(runtime=dict(mode='existing_container'))),
                'bindings.json':dict(nodes={'a':dict(machine_id='a'*32,
                    container=dict(id='c'*64,image='sha256:'+'d'*64,self=True))}),
                'compiled.json':{'topology':{'groups':{'a/model':dict(node='a',host='host-a',root='/work/t')}}}}
        request=dict(task_ref='t',runtime_archive='unused.zip',input_directory='unused',
                     runtime_sha256='c'*64,bundle_sha256='d'*64)
        config={'tasks':{'t':dict(host='host-a',root='/work/t',allowed_root='/work',task_root='/tmp/delivery/t',self=True)}}
        with patch('deploy_eval_v2.readiness.verify_frozen',return_value=values),\
             patch('deploy_eval_v2.readiness.install_archive'),\
             patch('deploy_eval_v2.readiness.shutil.which',return_value=None),\
             patch('deploy_eval_v2.readiness.control_is_posix',return_value=True),\
             patch('deploy_eval_v2.readiness.local_machine_id',return_value='a'*32):
            # A self task needs no ssh binary at all.
            result=check_ready(request,config)
            self.assertEqual(result['readiness']['status'],'READY_FOR_HOST_PREFLIGHT')
            self.assertEqual(result['readiness']['blockers'],[])
            with patch('deploy_eval_v2.readiness.local_machine_id',return_value='b'*32):
                self.assertIn('SELF_MACHINE_ID_MISMATCH',check_ready(request,config)['readiness']['blockers'])
            config['tasks']['t'].pop('self')
            self.assertIn('CONTROL_ENTRY_SELF_REQUIRED',check_ready(request,config)['readiness']['blockers'])
            config['tasks']['t']['self']=True
            values['bindings.json']['nodes']['a']['container'].pop('self')
            self.assertIn('SELF_NODE_COUNT',check_ready(request,config)['readiness']['blockers'])

    def test_self_evaluation_tester_must_designate_the_control_container(self):
        values={'plan.json':dict(task_id='t',intent='deploy',
                    deployment=dict(runtime=dict(mode='existing_container')),
                    evaluation=dict(test_binding='tester')),
                'bindings.json':dict(nodes={'a':dict(machine_id='a'*32,
                        container=dict(id='c'*64,image='sha256:'+'d'*64,self=True))},
                    testers={'tester':dict(machine_id='a'*32,host='host-a',work_root='/work',
                        container=dict(id='e'*64,image='sha256:'+'d'*64))}),
                'compiled.json':{'topology':{'groups':{'a/model':dict(node='a',host='host-a',root='/work/t')}}}}
        request=dict(task_ref='t',runtime_archive='unused.zip',input_directory='unused',
                     runtime_sha256='c'*64,bundle_sha256='d'*64)
        config={'tasks':{'t':dict(host='host-a',root='/work/t',allowed_root='/work',task_root='/tmp/delivery/t',self=True)}}
        with patch('deploy_eval_v2.readiness.verify_frozen',return_value=values),\
             patch('deploy_eval_v2.readiness.install_archive'),\
             patch('deploy_eval_v2.readiness.shutil.which',return_value=None),\
             patch('deploy_eval_v2.readiness.control_is_posix',return_value=True),\
             patch('deploy_eval_v2.readiness.local_machine_id',return_value='a'*32):
            result=check_ready(request,config)
            self.assertIn('TESTER_CONTAINER_MISMATCH',result['readiness']['blockers'])
            values['bindings.json']['testers']['tester']['container']['id']='c'*64
            self.assertEqual(check_ready(request,config)['readiness']['blockers'],[])
            values['bindings.json']['testers']['tester']['container'].pop('image')
            self.assertEqual(check_ready(request,config)['readiness']['blockers'],[])

    def test_tester_on_a_remote_node_host_must_use_that_nodes_container(self):
        values={'plan.json':dict(task_id='t',intent='deploy',
                    deployment=dict(runtime=dict(mode='existing_container')),
                    evaluation=dict(test_binding='tester')),
                'bindings.json':dict(nodes={
                        'a':dict(machine_id='a'*32,host='host-a',
                                 container=dict(id='c'*64,image='sha256:'+'d'*64,self=True)),
                        'b':dict(machine_id='b'*32,host='host-b',python='/opt/venv/bin/python',
                                 container=dict(id='e'*64,image='sha256:'+'f'*64))},
                    testers={'tester':dict(machine_id='b'*32,host='host-b',work_root='/work',
                        container=dict(id='e'*64,image='sha256:'+'f'*64))}),
                'compiled.json':{'topology':{'groups':{
                    'a/model':dict(node='a',host='host-a',root='/work/t',self=True),
                    'b/model':dict(node='b',host='host-b',root='/work/tb')}}}}
        request=dict(task_ref='t',runtime_archive='unused.zip',input_directory='unused',
                     runtime_sha256='c'*64,bundle_sha256='d'*64)
        config={'tasks':{'t':dict(host='host-a',root='/work/t',allowed_root='/work',
                                  task_root='/tmp/delivery/t',self=True,
            nodes={'b':dict(host='host-b',root='/work/tb',allowed_root='/work')},
            tester=dict(host='host-b',root='/work/t',allowed_root='/work',python='/p'))}}
        with patch('deploy_eval_v2.readiness.verify_frozen',return_value=values),\
             patch('deploy_eval_v2.readiness.install_archive'),\
             patch('deploy_eval_v2.readiness.shutil.which',return_value='/ssh'),\
             patch('deploy_eval_v2.readiness.control_is_posix',return_value=True),\
             patch('deploy_eval_v2.readiness.local_machine_id',return_value='a'*32):
            self.assertEqual(check_ready(request,config)['readiness']['blockers'],[])
            values['bindings.json']['testers']['tester']['container']['id']='c'*64
            self.assertIn('TESTER_CONTAINER_MISMATCH',
                          check_ready(request,config)['readiness']['blockers'])

    def test_offline_readiness_requires_attested_tester_and_reports_no_remote_validation(self):
        for attested in (False,True):
            with self.subTest(attested=attested),tempfile.TemporaryDirectory() as folder:
                root=Path(folder)
                import deploy_eval_v2
                package=build_archive(Path(deploy_eval_v2.__file__).parent,root/'runtime.zip')
                plan,_=normalize_external_plan(external_plan())
                bindings=eval_bindings()
                if attested:
                    bindings['testers']['tester']['machine_id']='a'*32
                runtime=package['runtime_sha256']
                review=dict(schema_version='1.0',status='APPROVED',unresolved=[],evidence_refs=['review.json'],
                            inputs=review_inputs(plan,bindings,runtime))
                frozen=freeze_inputs(root/'inputs',plan,bindings,review,runtime)
                request=dict(task_ref='t',runtime_archive=str(root/'runtime.zip'),input_directory=str(root/'inputs'),
                             runtime_sha256=runtime,bundle_sha256=frozen['sha256'])
                config={'tasks':{'t':dict(host='example.invalid',root='/work/t',allowed_root='/work',task_root=str(root/'t'))}}
                with patch('deploy_eval_v2.readiness.shutil.which',return_value='/ssh'):
                    result=check_ready(request,config)
                self.assertEqual(result['readiness']['status'],'READY_FOR_HOST_PREFLIGHT' if attested else 'BLOCKED')
                self.assertEqual(result['readiness']['remote_checks'],'NOT_RUN')
                self.assertFalse(result['started'])
