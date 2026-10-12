import copy
import unittest
from control_upgrade_onboarding_runtime_20261010 import accept


class RuntimeGuards(unittest.TestCase):
    def setUp(self):
        self.expected={'source_files':530,'source_manifest_sha256':'a'*64,'python':'3.12.15','openssl':'verified',
                       'tests':11,'entry_points_imported':['run_self_service_onboarding_analytics.py']}
        self.proof={**self.expected,'passed':True,'missing_credentials_rejected':True,'verified_tls_readonly_connection':True,
                    'normal_onboarding_executed':False,'financial_rows_read':0,'database_writes':0,'messages_sent':0,'arm_mutations':0}

    def test_exact_proof(self):accept(self.proof,self.expected)
    def test_wrong_source_and_runtime_rejected(self):
        for key in self.expected:
            p=copy.deepcopy(self.proof);p[key]=None
            with self.subTest(key=key),self.assertRaises(ValueError):accept(p,self.expected)
    def test_missing_tls_or_failure_rejected(self):
        for key in ('passed','verified_tls_readonly_connection','missing_credentials_rejected'):
            p=copy.deepcopy(self.proof);p[key]=False
            with self.subTest(key=key),self.assertRaises(ValueError):accept(p,self.expected)
    def test_real_actions_rejected(self):
        for key in ('financial_rows_read','database_writes','messages_sent','arm_mutations'):
            p=copy.deepcopy(self.proof);p[key]=1
            with self.subTest(key=key),self.assertRaises(ValueError):accept(p,self.expected)
    def test_boolean_not_zero_counter(self):
        p=copy.deepcopy(self.proof);p['database_writes']=False
        with self.assertRaises(ValueError):accept(p,self.expected)
    def test_normal_onboarding_forbidden(self):
        p=copy.deepcopy(self.proof);p['normal_onboarding_executed']=True
        with self.assertRaises(ValueError):accept(p,self.expected)


if __name__=='__main__':unittest.main()
