import copy
import unittest
from unittest.mock import patch
import retire_upgrade_additional_diagnostics_20261010 as r


class RetirementGuards(unittest.TestCase):
    def job(self,name='ibkrv2stg-ownertrial-log-canary'):
        return {'name':name,'location':'eastasia','identity':{'type':'None'},'properties':{
            'environmentId':'preserved-environment','configuration':{'triggerType':'Manual','secrets':[]},
            'template':{'containers':[{'name':'test','image':r.expected_image(name),'env':[]}]}}}

    def test_scope_is_exact_seven(self):
        self.assertEqual(7,len(r.APPROVED))
        for name in r.APPROVED:self.assertTrue(r.path_for(name).endswith('/'+name))
        for name in ('ibkrv2stg-web','ibkrv2stg-onboard-analytics','ibkrv2stg-inbox-acceptance','ibkrv2stg-import-u16088571','../jobs'):
            with self.subTest(name=name),self.assertRaises(ValueError):r.path_for(name)

    def test_no_identity_is_restorable(self):
        body=r.restore_body(self.job());self.assertEqual(body['identity'],{'type':'None'})
        self.assertEqual(body['properties']['template']['containers'][0]['image'],r.CANARY)

    def test_user_assigned_identity_preserved_not_recreated(self):
        job=self.job('ibkrv2stg-ownmid-20261005')
        job['identity']={'type':'UserAssigned','userAssignedIdentities':{'existing-id':{'principalId':'old'}}}
        body=r.restore_body(job)
        self.assertEqual(body['identity'],{'type':'UserAssigned','userAssignedIdentities':{'existing-id':{}}})

    def test_system_assigned_identity_not_authorized(self):
        job=self.job();job['identity']={'type':'SystemAssigned'}
        with self.assertRaises(ValueError):r.restore_body(job)

    def test_image_drift_not_restorable(self):
        job=self.job();job['properties']['template']['containers'][0]['image']='other'
        with self.assertRaises(ValueError):r.restore_body(job)

    def test_inline_secret_rejected(self):
        job=self.job();job['properties']['configuration']['secrets']=[{'name':'x','value':'not-archived'}]
        with self.assertRaises(ValueError):r.no_inline_secrets(job)
        job=self.job();job['properties']['template']['containers'][0]['env']=[{'name':'DATABASE_URL','value':'not-archived'}]
        with self.assertRaises(ValueError):r.no_inline_secrets(job)

    def test_secret_reference_is_not_a_secret_value(self):
        job=self.job();job['properties']['template']['containers'][0]['env']=[{'name':'DATABASE_URL','secretRef':'existing'}]
        r.no_inline_secrets(job)

    def test_other_workload_drift_blocks_deletion(self):
        with patch.object(r,'bindings',return_value={'web':'changed'}):
            with self.assertRaises(ValueError):r.preserve_others([],{'non_target_bindings':{'web':'original'}})


if __name__=='__main__':unittest.main()
