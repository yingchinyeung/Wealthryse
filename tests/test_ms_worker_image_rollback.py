import copy
import unittest

import rehearse_ms_worker_image_rollback as r


class RollbackScopeTests(unittest.TestCase):
    def setUp(self):
        identity=r.BASE+'Microsoft.ManagedIdentity/userAssignedIdentities/existing'
        self.job={'name':'production','location':'eastasia',
            'identity':{'type':'UserAssigned','userAssignedIdentities':{identity:{'principalId':'metadata'}}},
            'properties':{'environmentId':r.BASE+'Microsoft.App/managedEnvironments/existing',
                'configuration':{'triggerType':'Schedule','replicaRetryLimit':2,'replicaTimeout':10800,
                    'scheduleTriggerConfig':{'cronExpression':'0 8 1 * *'},'eventTriggerConfig':None,
                    'manualTriggerConfig':None,'secrets':[{'name':'db','value':None,
                        'keyVaultUrl':'https://vault/secrets/app','identity':identity}],
                    'registries':[{'server':'registry','identity':identity}]},
                'template':{'containers':[{'name':'report','image':'registry/base@sha256:'+'1'*64,
                    'command':['python'],'args':['normal-worker.py'],'env':[{'name':'DATABASE_URL','secretRef':'db'}],
                    'resources':{'cpu':1.0,'memory':'2Gi'}}],'initContainers':None,'volumes':None}}}

    def scratch(self):
        job=r.definition(self.job)
        job.update(name=r.NAME,id=r.TARGET)
        return job

    def test_manual_inert_definition_preserves_source_and_identity(self):
        old=copy.deepcopy(self.job)
        definition=r.definition(self.job)
        self.assertEqual(self.job,old)
        config=definition['properties']['configuration']
        self.assertEqual(config['triggerType'],'Manual')
        self.assertEqual(config['replicaRetryLimit'],0)
        self.assertNotIn('scheduleTriggerConfig',config)
        self.assertNotIn('eventTriggerConfig',config)
        self.assertEqual(definition['properties']['template']['containers'][0]['args'],['-c',r.CHECK])
        self.assertEqual(set(definition['identity']['userAssignedIdentities']),set(self.job['identity']['userAssignedIdentities']))

    def test_inline_secret_rejected(self):
        self.job['properties']['configuration']['secrets'][0]['value']='not-a-real-secret'
        with self.assertRaisesRegex(ValueError,'Inline secret bytes'):
            r.definition(self.job)

    def test_non_key_vault_secret_rejected(self):
        del self.job['properties']['configuration']['secrets'][0]['keyVaultUrl']
        with self.assertRaisesRegex(ValueError,'Key Vault reference'):
            r.definition(self.job)

    def test_init_container_rejected(self):
        self.job['properties']['template']['initContainers']=[{'name':'other'}]
        with self.assertRaisesRegex(ValueError,'volume-free'):
            r.definition(self.job)

    def test_new_system_identity_rejected(self):
        self.job['identity']['type']='SystemAssigned'
        with self.assertRaisesRegex(ValueError,'Existing user identity'):
            r.definition(self.job)

    def test_exact_scratch_scope_passes(self):
        r.scope(self.scratch())

    def test_production_name_cannot_be_mutated(self):
        job=self.scratch();job['name']=r.build.JOBS[1]
        with self.assertRaisesRegex(ValueError,'target identity'):
            r.scope(job)

    def test_wrong_resource_id_rejected(self):
        job=self.scratch();job['id']=r.BASE+'Microsoft.App/jobs/'+r.build.JOBS[0]
        with self.assertRaisesRegex(ValueError,'target identity'):
            r.scope(job)

    def test_schedule_reintroduced_rejected(self):
        job=self.scratch();job['properties']['configuration']['scheduleTriggerConfig']={'cronExpression':'* * * * *'}
        with self.assertRaisesRegex(ValueError,'command or trigger'):
            r.scope(job)

    def test_normal_worker_command_rejected(self):
        job=self.scratch();job['properties']['template']['containers'][0]['args']=['normal-worker.py']
        with self.assertRaisesRegex(ValueError,'command or trigger'):
            r.scope(job)

    def test_embedded_check_has_no_db_connection_or_entrypoint_invocation(self):
        compile(r.CHECK,'inert-recovery-check','exec')
        self.assertNotIn('psycopg.connect',r.CHECK)
        self.assertNotIn('.main(',r.CHECK)
        self.assertNotIn('assert ',r.CHECK)

    def test_four_empty_projection_defaults_are_normalized_without_mutation(self):
        wanted=r.definition(self.job)
        actual=copy.deepcopy(wanted)
        registry=actual['properties']['configuration']['registries'][0]
        registry.update(username='',passwordSecretRef='')
        container=actual['properties']['template']['containers'][0]
        container['resources']['ephemeralStorage']=''
        wanted['properties']['template']['containers'][0]['env'][0]['value']=''
        original=copy.deepcopy(actual)
        self.assertEqual(r.reference_settings(wanted),r.reference_settings(actual))
        self.assertEqual(actual,original)

    def test_real_registry_credentials_not_normalized_away(self):
        wanted=r.definition(self.job);actual=copy.deepcopy(wanted)
        actual['properties']['configuration']['registries'][0]['passwordSecretRef']='different-reference'
        self.assertNotEqual(r.reference_settings(wanted),r.reference_settings(actual))

    def test_resource_or_database_credential_changes_not_normalized_away(self):
        for field in ('resources','credential'):
            with self.subTest(field=field):
                wanted=r.definition(self.job);actual=copy.deepcopy(wanted)
                container=actual['properties']['template']['containers'][0]
                if field=='resources':container['resources']['ephemeralStorage']='1Gi'
                else:container['env'][0]['secretRef']='admin-url'
                self.assertNotEqual(r.reference_settings(wanted),r.reference_settings(actual))

    def test_patch_empty_storage_projection_only(self):
        baseline=self.scratch()
        baseline['properties']['template']['containers'][0]['resources']['ephemeralStorage']=''
        before=copy.deepcopy(baseline)
        actual=copy.deepcopy(baseline)
        actual['properties']['template']['containers'][0]['resources'].pop('ephemeralStorage')
        actual['properties']['template']['containers'][0]['image']='registry/candidate'
        snapshot={'job':baseline,'invariant_sha256':r.build.invariant(baseline)}
        self.assertTrue(r.settings_match(actual,snapshot,snapshot['invariant_sha256']))
        self.assertEqual(baseline,before)

    def test_patch_nonempty_storage_change_blocks(self):
        baseline=self.scratch();actual=copy.deepcopy(baseline)
        actual['properties']['template']['containers'][0]['resources']['ephemeralStorage']='1Gi'
        self.assertFalse(r.settings_match(actual,{'job':baseline,'invariant_sha256':r.build.invariant(baseline)}))

    def test_patch_does_not_ignore_other_empty_metadata_changes(self):
        baseline=self.scratch();actual=copy.deepcopy(baseline)
        actual['properties']['template']['containers'][0]['env'][0]['value']=''
        self.assertFalse(r.settings_match(actual,{'job':baseline,'invariant_sha256':r.build.invariant(baseline)}))

    def test_modified_raw_baseline_blocks(self):
        baseline=self.scratch()
        with self.assertRaisesRegex(ValueError,'Raw archived baseline'):
            r.settings_match(baseline,{'job':baseline,'invariant_sha256':'0'*64})

    def test_modified_intent_baseline_blocks(self):
        baseline=self.scratch()
        with self.assertRaisesRegex(ValueError,'Intent baseline'):
            r.settings_match(baseline,{'job':baseline,'invariant_sha256':r.build.invariant(baseline)},'0'*64)
