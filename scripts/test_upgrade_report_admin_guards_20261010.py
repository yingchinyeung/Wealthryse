"""Local fail-closed acceptance and exact scope regression tests."""
import copy,unittest
from unittest.mock import patch
import control_upgrade_report_admin_20261010 as c


class ReportAdminGuards(unittest.TestCase):
    def setUp(self):
        c.configure()
        self.expected={'source_files':50,'source_manifest_sha256':'a'*64,'python':'3.12.15','openssl':'test',
            'tests':12,'entry_points_imported':['freeze_month_end_snapshot.py']}
        self.proof={**self.expected,'passed':True,'missing_credentials_rejected':True,'verified_tls_readonly_connection':True,
            'financial_rows_read':0,'database_writes':0,'messages_sent':0,'arm_mutations':0,'normal_workflow_executed':False}
    def test_exact_scope(self):
        self.assertEqual({s['job'] for s in c.TARGETS.values()},{'ibkrv2stg-report-snapshot','ibkrv2stg-review-registration'})
    def test_good_proof(self):c.control.accept(self.proof,self.expected)
    def test_financial_reads_rejected(self):
        with self.assertRaises(ValueError):c.control.accept({**self.proof,'financial_rows_read':1},self.expected)
    def test_writes_rejected(self):
        for key in ('database_writes','messages_sent','arm_mutations'):
            with self.subTest(key=key),self.assertRaises(ValueError):c.control.accept({**self.proof,key:1},self.expected)
    def test_bool_not_zero(self):
        with self.assertRaises(ValueError):c.control.accept({**self.proof,'database_writes':False},self.expected)
    def test_source_drift(self):
        with self.assertRaises(ValueError):c.control.accept({**self.proof,'source_manifest_sha256':'b'*64},self.expected)
    def test_tls_required(self):
        with self.assertRaises(ValueError):c.control.accept({**self.proof,'verified_tls_readonly_connection':False},self.expected)
    def test_normal_job_not_allowed(self):
        with self.assertRaises(ValueError):c.control.accept({**self.proof,'normal_workflow_executed':True},self.expected)
    def test_failed_trial_rejected(self):
        with self.assertRaises(ValueError):c.deploy.validate_trial({'passed':False,'image':'i','settings_preserved':True,'proof':self.proof},'i',self.expected)
    def test_wrong_image_rejected(self):
        with self.assertRaises(ValueError):c.deploy.validate_trial({'passed':True,'image':'other','settings_preserved':True,'proof':self.proof},'i',self.expected)
    def test_settings_drift(self):
        with self.assertRaises(ValueError):c.deploy.validate_trial({'passed':True,'image':'i','settings_preserved':False,'proof':self.proof},'i',self.expected)
    def test_unknown_scope(self):
        with self.assertRaises(ValueError):c.main('runtime','website','start')
    def test_scheduled_job_rejected(self):
        job={'properties':{'configuration':{'triggerType':'Schedule'},'template':{'containers':[{'image':c.release.source_image('snapshot'),
            'command':['python'],'args':['scripts/freeze_month_end_snapshot.py']}]}}}
        with patch.object(c.release,'request',return_value=job),self.assertRaises(ValueError):c.release.snapshot('snapshot')
    def drift_case(self,change=None,allowed=False):
        baseline={s['job']:{'type':'job','invariant':'fixed','images':[c.release.source_image(t)]} for t,s in c.TARGETS.items()}
        baseline['website']={'type':'app','invariant':'fixed','images':['website-original']}
        current=copy.deepcopy(baseline)
        if change:change(current)
        def verify(folder):
            t=folder.name.removesuffix('-security-v1')
            return {'base_image':c.release.source_image(t)},None,{'registry':'test','repository':'app','digest':t}
        with patch.object(c.control,'protected',return_value={'bindings':baseline}),patch.object(c,'current_resources',return_value=[]),patch.object(c,'bindings',return_value=current),patch.object(c.scanner,'verify',side_effect=verify):
            if allowed:c.predeployment_bindings()
            else:
                with self.assertRaises(ValueError):c.predeployment_bindings()
    def test_full_inventory_stable(self):self.drift_case(allowed=True)
    def test_known_paired_deployment_allowed(self):self.drift_case(lambda x:x['ibkrv2stg-report-snapshot'].update(images=['test/app@snapshot']),True)
    def test_external_website_deployment_blocked(self):self.drift_case(lambda x:x['website'].update(images=['other']))
    def test_same_job_config_drift_blocked(self):self.drift_case(lambda x:x['ibkrv2stg-report-snapshot'].update(invariant='other'))
    def test_new_workload_blocked(self):self.drift_case(lambda x:x.update(unexpected={}))


if __name__=='__main__':unittest.main()
