import copy
import unittest

import rehearse_ms_website_release_rollback as rollback
import continue_ms_website_release_rollback as continuation


def fixture():
    identity='managed-identity-fixture'
    job={'location':'eastasia','identity':{'type':'UserAssigned','userAssignedIdentities':{identity:{}}},
        'properties':{'environmentId':'environment-fixture','configuration':{
            'registries':[{'server':'registry-fixture','identity':identity}],
            'secrets':[{'name':'database','keyVaultUrl':'private-fixture'}]},
            'template':{'containers':[{'image':'baseline@sha256:'+'a'*64,'env':[
                {'name':'AUTHORIZATION_MODE','value':'database'},
                {'name':'DATABASE_URL','secretRef':'database'},
                {'name':'APPROVAL_DATABASE_URL','secretRef':'administrator'},
                {'name':'ALLOWED_EMAILS','value':'owner-fixture'}]}]}}}
    return {'manual_job':copy.deepcopy(job),'website':copy.deepcopy(job),'benchmark_job':copy.deepcopy(job)}


class RollbackScope(unittest.TestCase):
    def test_observed_projection_defaults_only(self):
        planned=rollback.definition(fixture(),'web')
        actual=copy.deepcopy(planned)
        actual['properties']['configuration'].pop('secrets')
        identity=actual['identity']['userAssignedIdentities']['managed-identity-fixture']
        identity.update(clientId='read-only-client',principalId='read-only-principal')
        self.assertEqual(continuation.non_image(actual),continuation.non_image(planned))
        for field in ('identity','registry','secret','environment'):
            changed=copy.deepcopy(actual)
            if field=='identity':changed['identity']['userAssignedIdentities']['other-identity']={}
            elif field=='registry':changed['properties']['configuration']['registries'][0]['identity']='other-identity'
            elif field=='secret':changed['properties']['configuration']['secrets']=[{'name':'unexpected'}]
            else:changed['properties']['environmentId']='other-environment'
            with self.subTest(field=field):
                self.assertNotEqual(continuation.non_image(changed),continuation.non_image(planned))

    def test_definition_is_manual_only_and_has_no_financial_credentials(self):
        for kind in rollback.NAMES:
            result=rollback.definition(fixture(),kind)
            result.update(id=rollback.path(kind),name=rollback.NAMES[kind])
            rollback.scope(result,kind)
            self.assertEqual(result['properties']['configuration']['secrets'],[])
            names={e['name'] for e in result['properties']['template']['containers'][0]['env']}
            self.assertNotIn('DATABASE_URL',names)
            self.assertNotIn('APPROVAL_DATABASE_URL',names)
            self.assertEqual(result['properties']['configuration']['replicaRetryLimit'],0)

    def test_image_only_switch_keeps_non_image_invariant(self):
        original=rollback.definition(fixture(),'web')
        changed=copy.deepcopy(original);changed['properties']['template']['containers'][0]['image']='another@sha256:'+'b'*64
        self.assertEqual(rollback.non_image(original),rollback.non_image(changed))
        changed['properties']['template']['containers'][0]['env'][0]['value']='legacy'
        self.assertNotEqual(rollback.non_image(original),rollback.non_image(changed))

    def test_scope_rejects_wrong_resource_schedule_and_extra_database_environment(self):
        value=rollback.definition(fixture(),'web');value.update(id=rollback.path('web'),name=rollback.NAMES['web'])
        for change in ('id','schedule','secret','database','role','duplicate','command'):
            changed=copy.deepcopy(value);container=changed['properties']['template']['containers'][0]
            if change=='id':changed['id']='a-live-website'
            elif change=='schedule':changed['properties']['configuration']['triggerType']='Schedule'
            elif change=='secret':changed['properties']['configuration']['secrets']=[{'name':'unexpected'}]
            elif change=='database':container['env'].append({'name':'DATABASE_URL','value':'unexpected'})
            elif change=='role':container['env'][-1]['value']='benchmark'
            elif change=='duplicate':container['env'].append(copy.deepcopy(container['env'][0]))
            elif change=='command':container['args']=['scripts/run_registry_month_end_packages.py']
            with self.subTest(change=change),self.assertRaises(ValueError):rollback.scope(changed,'web')
