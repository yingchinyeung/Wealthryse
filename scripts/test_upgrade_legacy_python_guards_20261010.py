"""No-network regressions for stage24 scope, immutable fallback and drift gates."""
import copy,unittest
from unittest.mock import patch
import control_upgrade_legacy_python_20261010 as m


class LegacyGuards(unittest.TestCase):
    def setUp(self):
        m.configure()
        self.expected={'source_files':68,'source_manifest_sha256':'a'*64,'python':'3.12.15','openssl':'test','tests':12,'entry_points_imported':['import_pilot_snapshot.py']}
        self.proof={**self.expected,'passed':True,'missing_credentials_rejected':True,'verified_tls_readonly_connection':True,
            'financial_rows_read':0,'database_writes':0,'messages_sent':0,'arm_mutations':0,'normal_workflow_executed':False}
    def test_scope(self):self.assertEqual({s['job'] for s in m.TARGETS.values()},{'ibkrv2stg-internal-release','ibkrv2stg-import-u16088571','ibkrv2stg-verify-u16088571'})
    def test_tests(self):self.assertEqual([s['tests'] for s in m.TARGETS.values()],[20,12,14])
    def test_original_tag_retained(self):self.assertTrue(m.original('pilotimport').endswith(':789e7d4'))
    def test_fallback_immutable(self):
        for target in m.TARGETS:self.assertIn('@sha256:',m.r.source_image(target))
    def test_tag_resolution_pass(self):
        with patch.object(m.c,'az',return_value={'digest':'sha256:'+m.TARGETS['pilotimport']['digest']}):m.verify_tag('pilotimport')
    def test_tag_drift_rejected(self):
        with patch.object(m.c,'az',return_value={'digest':'sha256:wrong'}),self.assertRaises(ValueError):m.verify_tag('pilotimport')
    def test_pinned_no_tag_lookup(self):
        with patch.object(m.c,'az') as az:m.verify_tag('internalrelease');az.assert_not_called()
    def test_good_proof(self):m.c.accept(self.proof,self.expected)
    def test_financial_reads_rejected(self):
        with self.assertRaises(ValueError):m.c.accept({**self.proof,'financial_rows_read':1},self.expected)
    def test_writes_rejected(self):
        for key in ('database_writes','messages_sent','arm_mutations'):
            with self.subTest(key=key),self.assertRaises(ValueError):m.c.accept({**self.proof,key:1},self.expected)
    def test_bool_not_zero(self):
        with self.assertRaises(ValueError):m.c.accept({**self.proof,'database_writes':False},self.expected)
    def test_source_drift(self):
        with self.assertRaises(ValueError):m.c.accept({**self.proof,'source_manifest_sha256':'b'*64},self.expected)
    def test_tls_required(self):
        with self.assertRaises(ValueError):m.c.accept({**self.proof,'verified_tls_readonly_connection':False},self.expected)
    def test_normal_workflow_rejected(self):
        with self.assertRaises(ValueError):m.c.accept({**self.proof,'normal_workflow_executed':True},self.expected)
    def test_failed_trial(self):
        with self.assertRaises(ValueError):m.validate_trial({'passed':False,'image':'i','settings_preserved':True,'proof':self.proof},'i',self.expected)
    def test_wrong_trial_image(self):
        with self.assertRaises(ValueError):m.validate_trial({'passed':True,'image':'other','settings_preserved':True,'proof':self.proof},'i',self.expected)
    def test_trial_settings_drift(self):
        with self.assertRaises(ValueError):m.validate_trial({'passed':True,'image':'i','settings_preserved':False,'proof':self.proof},'i',self.expected)
    def test_unknown_scope(self):
        for action,args in [('deploy',('website','apply')),('runtime',('website','start')),('scan',('website',))]:
            with self.subTest(action=action),self.assertRaises(ValueError):m.main(action,*args)
    def test_unknown_deploy_stage(self):
        with self.assertRaises(ValueError):m.deploy('pilotimport','rollback-unreviewed')
    def test_scheduled_job_rejected(self):
        job={'properties':{'configuration':{'triggerType':'Schedule'},'template':{'containers':[{'image':m.original('internalrelease'),'command':['python'],'args':['scripts/release_internal_reports.py']}]}}}
        with patch.object(m.c,'request',return_value=job),self.assertRaises(ValueError):m.snapshot('internalrelease')
    def drift(self,change=None,allowed=False):
        baseline={s['job']:{'type':'job','invariant':'fixed','images':[m.original(t)]} for t,s in m.TARGETS.items()}
        baseline['website']={'type':'app','invariant':'fixed','images':['original']};current=copy.deepcopy(baseline)
        if change:change(current)
        def verify(folder):return None,None,{'registry':'test','repository':'app','digest':folder.name.removesuffix('-security-v1')}
        with patch.object(m.c,'protected',return_value={'bindings':baseline}),patch.object(m,'current_resources',return_value=[]),patch.object(m,'bindings',return_value=current),patch.object(m.scanner,'verify',side_effect=verify):
            if allowed:m.bindings_gate()
            else:
                with self.assertRaises(ValueError):m.bindings_gate()
    def test_inventory_unchanged(self):self.drift(allowed=True)
    def test_scoped_deployment_allowed(self):self.drift(lambda x:x['ibkrv2stg-import-u16088571'].update(images=['test/app@pilotimport']),True)
    def test_external_deployment_blocked(self):self.drift(lambda x:x['website'].update(images=['other']))
    def test_same_job_settings_drift(self):self.drift(lambda x:x['ibkrv2stg-import-u16088571'].update(invariant='other'))
    def test_inventory_drift(self):self.drift(lambda x:x.update(unexpected={}))


if __name__=='__main__':unittest.main()
