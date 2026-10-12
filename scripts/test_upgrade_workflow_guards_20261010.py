"""Fail-closed controller and two-job boundary regressions; no Azure calls."""
import copy,unittest
from unittest.mock import patch
import control_upgrade_workflow_runtime_20261010 as c
import deploy_upgrade_workflow_security_20261010 as d
import release_upgrade_workflow_security_20261010 as r


class WorkflowGuards(unittest.TestCase):
    def setUp(self):
        self.expected={'source_files':123,'source_manifest_sha256':'a'*64,'python':'3.12.15','openssl':'synthetic',
            'tests':14,'entry_points_imported':['register_account_template_intake.py']}
        self.proof={**self.expected,'passed':True,'missing_credentials_rejected':True,'verified_tls_readonly_connection':True,
            'financial_rows_read':0,'database_writes':0,'messages_sent':0,'arm_mutations':0,'normal_workflow_executed':False}

    def test_good_readonly_proof(self):c.accept(self.proof,self.expected)
    def test_mutations_rejected(self):
        for key in ('financial_rows_read','database_writes','messages_sent','arm_mutations'):
            with self.subTest(key=key),self.assertRaises(ValueError):c.accept({**self.proof,key:1},self.expected)
    def test_boolean_zero_rejected(self):
        with self.assertRaises(ValueError):c.accept({**self.proof,'database_writes':False},self.expected)
    def test_missing_tls_rejected(self):
        with self.assertRaises(ValueError):c.accept({**self.proof,'verified_tls_readonly_connection':False},self.expected)
    def test_source_drift_rejected(self):
        with self.assertRaises(ValueError):c.accept({**self.proof,'source_manifest_sha256':'b'*64},self.expected)
    def test_normal_workflow_rejected(self):
        with self.assertRaises(ValueError):c.accept({**self.proof,'normal_workflow_executed':True},self.expected)
    def test_scope_exact(self):
        self.assertEqual({x['job'] for x in r.TARGETS.values()},{'ibkrv2stg-template-intake','ibkrv2stg-institutional-shadow'})
        with self.assertRaises(ValueError):c.main('website','start')
    def test_failed_or_wrong_image_trial_rejected(self):
        base={'passed':True,'image':'expected','settings_preserved':True,'proof':self.proof}
        for field,value in [('passed',False),('image','wrong'),('settings_preserved',False)]:
            with self.subTest(field=field),self.assertRaises(ValueError):d.deploy.validate_trial({**base,field:value},'expected',self.expected)
    def test_config_drift_rejected(self):
        job={'properties':{'configuration':{'triggerType':'Schedule'},'template':{'containers':[{'image':r.source_image('intake'),
            'command':['python'],'args':['scripts/register_account_template_intake.py']}]}}}
        with patch.object(r,'request',return_value=job),self.assertRaises(ValueError):r.snapshot('intake')
    def test_adapter_preserves_controller_targets(self):
        self.assertIsInstance(c.TARGETS['intake'],dict)
        self.assertEqual(d.deploy.c.TARGETS['intake'][0],'ibkrv2stg-template-intake')


if __name__=='__main__':unittest.main()
