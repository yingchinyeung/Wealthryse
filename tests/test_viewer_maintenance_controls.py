"""Maintenance guard regression checks without Azure access."""
import copy,unittest
from qualify_viewer_database_recovery_20261008 import scope,SOURCE,PATH,restored_database_url
from deploy_viewer_database_ha_20261008 import paused_configuration,invariant,job_invariant,window
from datetime import datetime,timedelta,timezone

class MaintenanceScopeTests(unittest.TestCase):
    def setUp(self):
        self.server={'id':SOURCE,'sku':{'name':'Standard_B1ms','tier':'Burstable'},'location':'eastasia','properties':{'network':{'publicNetworkAccess':'Disabled','delegatedSubnetResourceId':'private-subnet'},'highAvailability':{'mode':'Disabled'},'storage':{'storageSizeGB':32},'version':'16','backup':{'backupRetentionDays':14},'administratorLogin':'not-a-password','state':'Ready'}}
    def test_scope_excludes_login_and_operational_state(self):
        value=scope(self.server)
        self.assertNotIn('administratorLogin',value)
        self.assertNotIn('state',value)
    def test_network_and_backup_drift_are_guarded(self):
        for key in ('network','backup','storage','highAvailability'):
            changed=copy.deepcopy(self.server);changed['properties'][key]={'unexpected':'drift'}
            self.assertNotEqual(scope(self.server),scope(changed))
    def test_tier_and_version_drift_are_guarded(self):
        changed=copy.deepcopy(self.server);changed['sku']['tier']='GeneralPurpose'
        self.assertNotEqual(scope(self.server),scope(changed))
        changed=copy.deepcopy(self.server);changed['properties']['version']='17'
        self.assertNotEqual(scope(self.server),scope(changed))
    def test_recovery_target_is_distinct_and_same_resource_group(self):
        self.assertNotEqual(SOURCE,PATH)
        self.assertEqual(SOURCE.split('/providers/')[0],PATH.split('/providers/')[0])
        self.assertTrue(PATH.endswith('/ibkrv2stg-app-recovery-20261008'))
    def test_clone_override_preserves_website_uri_and_encoded_credentials(self):
        original='postgresql://fixture:p%40ss%3Aword@ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com:5432/fixture?sslmode=require&application_name=readonly'
        expected=original.replace('ibkrv2stg-pg-auhecwc4itaoo.','ibkrv2stg-app-recovery-20261008.')
        self.assertEqual(restored_database_url(original),expected)
        self.assertTrue(restored_database_url(original).startswith('postgresql://'))
    def test_clone_override_refuses_keyword_dsn_and_other_hosts(self):
        for original in ('host=unexpected password=fixture','postgresql://u:p@unexpected/db','https://u:p@ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com/db'):
            with self.assertRaises(ValueError):restored_database_url(original)

class MaintenanceScheduleTests(unittest.TestCase):
    def setUp(self):
        self.job={'identity':{'type':'UserAssigned'},'properties':{'environmentId':'private-environment','configuration':{'triggerType':'Schedule','scheduleTriggerConfig':{'cronExpression':'*/5 * * * *','parallelism':1,'replicaCompletionCount':1},'secrets':[{'name':'database-url','keyVaultUrl':'https://example.vault.azure.net/secrets/app','identity':'existing-identity'}]},'template':{'containers':[{'image':'registry@sha256:fixed','name':'existing-worker','command':['python'],'args':['existing.py'],'env':[{'name':'DATABASE_URL','secretRef':'database-url'}],'resources':{'cpu':0.5,'memory':'1Gi'}}]}}}
    def test_pause_changes_only_trigger_and_timing(self):
        paused=copy.deepcopy(self.job);paused['properties']['configuration']=paused_configuration(self.job)
        self.assertEqual(job_invariant(paused),job_invariant(self.job))
        self.assertEqual(paused['properties']['configuration']['triggerType'],'Manual')
        self.assertNotIn('scheduleTriggerConfig',paused['properties']['configuration'])
        self.assertEqual(self.job['properties']['configuration']['triggerType'],'Schedule')
    def test_refuses_inline_secrets_and_non_scheduled_jobs(self):
        inline=copy.deepcopy(self.job);inline['properties']['configuration']['secrets'][0]['value']='never-archive'
        with self.assertRaises(ValueError):paused_configuration(inline)
        manual=copy.deepcopy(self.job);manual['properties']['configuration']['triggerType']='Manual'
        with self.assertRaises(ValueError):paused_configuration(manual)
    def test_image_and_credential_drift_are_not_normalized(self):
        changed=copy.deepcopy(self.job);changed['properties']['template']['containers'][0]['image']='other-image'
        self.assertNotEqual(job_invariant(changed),job_invariant(self.job))
        changed=copy.deepcopy(self.job);changed['properties']['configuration']['secrets'][0]['keyVaultUrl']='other-secret'
        self.assertNotEqual(job_invariant(changed),job_invariant(self.job))
    def test_expired_window_refuses_mutations(self):
        with self.assertRaises(ValueError):window({'window_end':(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()})
        window({'window_end':(datetime.now(timezone.utc)+timedelta(minutes=1)).isoformat()})

if __name__=='__main__':unittest.main()
