"""Dispatcher/wrapper guards; not live PostgreSQL or fresh-login acceptance."""
import copy
import importlib.util
import pathlib
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import deploy_owner_task_postgres_trial as deploy
import run_owner_task_postgres_trial as runner


def job():
    identity = deploy.BASE+'Microsoft.ManagedIdentity/userAssignedIdentities/ibkrv2stg-ingestion-identity'
    return {'identity':{'type':'UserAssigned','userAssignedIdentities':{identity:{}}},'properties':{
        'configuration':{'triggerType':'Manual','replicaRetryLimit':0,
            'manualTriggerConfig':{'parallelism':1,'replicaCompletionCount':1},
            'secrets':[{'name':'database-admin-url','keyVaultUrl':
                'https://ibkrv2stg-auhecwc4itaoo.vault.azure.net/secrets/database-admin-url','identity':identity}]},
        'template':{'containers':[{'name':'postgres-bootstrap','image':
            'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:276a4783bf4ddf437bd5d449e08df39729fb43f449975082af80fd4c13e1142c',
            'command':['python'],'args':['old'],'env':[{'name':'UNRELATED','secretRef':'not-included'}],
            'resources':{'cpu':0.5,'memory':'1Gi'}}]}}}


class DispatcherTests(unittest.TestCase):
    def test_exact_existing_definition(self):
        deploy.validate_bootstrap(job())

    def test_changed_image_denied(self):
        value = job(); value['properties']['template']['containers'][0]['image'] = 'latest'
        with self.assertRaises(ValueError): deploy.validate_bootstrap(value)

    def test_retry_denied(self):
        value = job(); value['properties']['configuration']['replicaRetryLimit'] = 1
        with self.assertRaises(ValueError): deploy.validate_bootstrap(value)

    def test_schedule_denied(self):
        value = job(); value['properties']['configuration']['triggerType'] = 'Schedule'
        with self.assertRaises(ValueError): deploy.validate_bootstrap(value)

    def test_extra_identity_denied(self):
        value = job(); value['identity']['userAssignedIdentities']['extra'] = {}
        with self.assertRaises(ValueError): deploy.validate_bootstrap(value)

    def test_extra_container_denied(self):
        value = job(); value['properties']['template']['containers'].append({})
        with self.assertRaises(ValueError): deploy.validate_bootstrap(value)

    def test_volume_denied(self):
        value = job(); value['properties']['template']['volumes'] = [{'name':'shared'}]
        with self.assertRaises(ValueError): deploy.validate_bootstrap(value)

    def test_plain_secret_denied(self):
        value = job(); value['properties']['configuration']['secrets'][0]['value'] = 'sensitive'
        with self.assertRaises(ValueError): deploy.validate_bootstrap(value)

    def test_wrong_secret_scope_denied(self):
        value = job(); value['properties']['configuration']['secrets'][0]['keyVaultUrl'] += '-other'
        with self.assertRaises(ValueError): deploy.validate_bootstrap(value)

    def test_execution_override_contains_only_required_refs(self):
        value = job(); original = copy.deepcopy(value)
        with patch.object(deploy,'bundle',return_value=(['-c','fixed'],{'source':'hash'})):
            result,_ = deploy.template(value,'run')
        self.assertEqual(value,original)
        self.assertEqual(result['containers'][0]['env'],[
            {'name':'DATABASE_ADMIN_URL','secretRef':'database-admin-url'},
            {'name':'OWNER_TASK_TRIAL_STAGE','value':'run'}])

    def test_executed_internal_secret_alias_permitted(self):
        with patch.object(deploy,'bundle',return_value=(['-c','fixed'],{})):
            wanted,_ = deploy.template(job(),'run')
        actual = copy.deepcopy(wanted['containers']);actual[0]['env'][0]['secretRef'] = 'cappjob-'+deploy.JOB
        deploy.verify_containers(actual,wanted)

    def test_executed_value_secret_denied(self):
        with patch.object(deploy,'bundle',return_value=(['-c','fixed'],{})):
            wanted,_ = deploy.template(job(),'run')
        actual = copy.deepcopy(wanted['containers']);actual[0]['env'][0]['value'] = 'secret'
        with self.assertRaises(ValueError): deploy.verify_containers(actual,wanted)

    def test_executed_command_change_denied(self):
        with patch.object(deploy,'bundle',return_value=(['-c','fixed'],{})):
            wanted,_ = deploy.template(job(),'run')
        actual = copy.deepcopy(wanted['containers']);actual[0]['args'] = ['changed']
        with self.assertRaises(ValueError): deploy.verify_containers(actual,wanted)

    def test_false_receipt_fails_before_network(self):
        with patch.object(deploy,'request') as request:
            with self.assertRaises(ValueError): deploy.protected({'protected_receipt':{}},'run-intent')
        request.assert_not_called()

    def test_unknown_bundle_stage_denied(self):
        with self.assertRaises(ValueError): deploy.bundle('production')

    def test_truthy_nonboolean_hold_denied(self):
        with patch.object(deploy,'request',return_value={'id':deploy.SCOPE,'properties':{'hasLegalHold':'true'}}):
            with self.assertRaises(ValueError): deploy.archive_protection()

    def test_other_archive_identity_denied(self):
        with patch.object(deploy,'request',return_value={'id':deploy.SCOPE+'other','properties':{'hasLegalHold':True}}):
            with self.assertRaises(ValueError): deploy.archive_protection()

    def test_exact_private_held_archive(self):
        with patch.object(deploy,'request',return_value={'id':deploy.SCOPE,'properties':{'hasLegalHold':True,'publicAccess':'None'}}):
            deploy.archive_protection()

    def test_archive_helpers_are_frozen(self):
        self.assertIn('scripts/run_specialist_gateway_read_verification.py',deploy.FILES)
        self.assertIn('src/ibkr_etl/specialist_archive_reader.py',deploy.FILES)
        self.assertIn('src/ibkr_etl/specialist_audit_service.py',deploy.FILES)

    def test_exclusive_intent_reservation_has_one_winner(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(deploy,'FOLDER',pathlib.Path(folder)),\
                patch.object(deploy,'archive_protection'),patch.object(deploy,'retain',return_value={'synthetic':True}) as retain:
            def attempt():
                try:
                    deploy.save({'intent':'synthetic'},'run-intent.json','run-intent')
                    return 'winner'
                except FileExistsError:
                    return 'denied'
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _:attempt(),range(2)))
            self.assertEqual(sorted(results),['denied','winner'])
            self.assertEqual(retain.call_count,1)

    def test_failed_archive_keeps_uncertain_reservation(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(deploy,'FOLDER',pathlib.Path(folder)),\
                patch.object(deploy,'archive_protection',side_effect=ValueError('unavailable')):
            with self.assertRaises(ValueError): deploy.save({'intent':'synthetic'},'run-intent.json','run-intent')
            self.assertTrue((pathlib.Path(folder)/'run-intent.json').exists())
            with self.assertRaises(FileExistsError): deploy.save({'intent':'synthetic'},'run-intent.json','run-intent')


class WrapperGuardTests(unittest.TestCase):
    def url(self,**changes):
        values = dict(host=runner.HOST,dbname='ibkr_staging',port='5432',sslmode='require',user='fake',password='synthetic')
        values.update(changes)
        return ' '.join(k+'='+v for k,v in values.items())

    def test_exact_url(self):
        self.assertEqual(runner.validate_admin_url(self.url())['host'],runner.HOST)

    def test_other_host_denied(self):
        with self.assertRaises(ValueError): runner.validate_admin_url(self.url(host='other'))

    def test_hostaddr_bypass_denied(self):
        with self.assertRaises(ValueError): runner.validate_admin_url(self.url(hostaddr='127.0.0.1'))

    def test_other_database_denied(self):
        with self.assertRaises(ValueError): runner.validate_admin_url(self.url(dbname='postgres'))

    def test_ssl_disabled_denied(self):
        with self.assertRaises(ValueError): runner.validate_admin_url(self.url(sslmode='disable'))

    def test_options_bypass_denied(self):
        with self.assertRaises(ValueError): runner.validate_admin_url(self.url(options='unregistered'))

    def test_no_login_or_forced_cleanup_in_source(self):
        source = (ROOT/'scripts/run_owner_task_postgres_trial.py').read_text()
        self.assertIn('CREATE ROLE {} NOLOGIN NOINHERIT',source)
        self.assertNotIn('DROP DATABASE {} WITH',source)
        self.assertNotIn('pg_terminate_backend',source)


if __name__ == '__main__':
    unittest.main()
