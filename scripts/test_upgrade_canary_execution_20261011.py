import copy,unittest
from unittest.mock import patch
import control_upgrade_private_canaries_v3_20261011 as c

class ExecutionGuard(unittest.TestCase):
    def setUp(self):
        self.template={'containers':[{'name':'probe','image':'qualified','env':[{'name':'CHECK_SECRET_000','secretRef':'qa-ref-000'}]}]}
        self.mock=patch.object(c.original,'definitions',return_value=({}, {'ibkrv2stg-netqa41-g17':{'properties':{'template':self.template}}}))
        self.mock.start();self.addCleanup(self.mock.stop)
        self.observed=copy.deepcopy(self.template)
        self.observed['containers'][0]['env'][0]['secretRef']='cappjob-ibkrv2stg-netqa41-g17'
    def test_observed_internal_alias(self):c.same(self.observed,self.template)
    def test_source_not_mutated(self):
        before=copy.deepcopy(self.observed);c.same(self.observed,self.template);self.assertEqual(before,self.observed)
    def test_other_alias_rejected(self):
        self.observed['containers'][0]['env'][0]['secretRef']='cappjob-other'
        with self.assertRaises(ValueError):c.same(self.observed,self.template)
    def test_values_rejected(self):
        self.observed['containers'][0]['env'][0]['value']='test-only'
        with self.assertRaises(ValueError):c.same(self.observed,self.template)
    def test_changed_image_rejected(self):
        self.observed['containers'][0]['image']='other'
        with self.assertRaises(ValueError):c.same(self.observed,self.template)
    def test_changed_name_rejected(self):
        self.observed['containers'][0]['env'][0]['name']='other'
        with self.assertRaises(ValueError):c.same(self.observed,self.template)
    def test_job_definition_alias_not_normalized(self):
        with self.assertRaises(ValueError):c.same({'properties':{'template':self.observed}},{'properties':{'template':self.template}})

if __name__=='__main__':unittest.main()
