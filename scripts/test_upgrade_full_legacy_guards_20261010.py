"""Offline stage25 guards: exact scope, full runtime and unchanged settings."""
import copy,unittest
from unittest.mock import patch
import control_upgrade_full_legacy_20261010 as m


class FullRuntimeGuards(unittest.TestCase):
    def setUp(self):
        m.configure();self.expected={'source_files':50,'source_manifest_sha256':'a'*64,'python':'3.12.15','openssl':'test','tests':20,'entry_points_imported':['inline-inbox-acceptance'],'node':'v24.21.0'}
        self.proof={**self.expected,'passed':True,'missing_credentials_rejected':True,'verified_tls_readonly_connection':True,'renderer_imported':True,
            'financial_rows_read':0,'database_writes':0,'messages_sent':0,'arm_mutations':0,'normal_workflow_executed':False}
    def test_exact_scope(self):self.assertEqual({s['job'] for s in m.TARGETS.values()},{'ibkrv2stg-historical-reports','ibkrv2stg-presentation-v23-hist','ibkrv2stg-inbox-acceptance','ibkrv2stg-inbox-acceptance-r2'})
    def test_inbox_shared_candidate(self):self.assertEqual(m.asset('inboxr2'),'inbox');self.assertEqual(m.source_image('inboxr2'),m.source_image('inbox'))
    def test_presentation_repository(self):self.assertIn('/ibkr-portfolio@sha256:',m.source_image('legacypresentation'))
    def test_good_proof(self):m.accept(self.proof,self.expected)
    def test_missing_node(self):
        with self.assertRaises(ValueError):m.accept({**self.proof,'node':None},self.expected)
    def test_wrong_node(self):
        with self.assertRaises(ValueError):m.accept({**self.proof,'node':'v20.19.2'},self.expected)
    def test_renderer_required(self):
        with self.assertRaises(ValueError):m.accept({**self.proof,'renderer_imported':False},self.expected)
    def test_no_financial_side_effects(self):
        for key in ('financial_rows_read','database_writes','messages_sent','arm_mutations'):
            with self.subTest(key=key),self.assertRaises(ValueError):m.accept({**self.proof,key:1},self.expected)
    def test_normal_workflow_rejected(self):
        with self.assertRaises(ValueError):m.accept({**self.proof,'normal_workflow_executed':True},self.expected)
    def test_unknown_target(self):
        with self.assertRaises(ValueError):m.asset('website')
    def evidence(self):return {'passed':True,'candidate_image':'candidate','original_image':m.source_image('legacypresentation'),'proofs':[{'stage':s,'passed':True,'slides':20,'parts':190,'normalized_package_sha256':'equal','snapshot_sha256':'input','template_sha256':'template'} for s in ('baseline','candidate')]}
    def test_equal_render_accepted(self):
        with patch.object(m.c,'protected',return_value=self.evidence()):m.render_gate('legacypresentation','candidate')
    def test_different_render_rejected(self):
        evidence=self.evidence();evidence['proofs'][1]['normalized_package_sha256']='different'
        with patch.object(m.c,'protected',return_value=evidence),self.assertRaises(ValueError):m.render_gate('legacypresentation','candidate')
    def test_other_candidate_render_rejected(self):
        with patch.object(m.c,'protected',return_value=self.evidence()),self.assertRaises(ValueError):m.render_gate('legacypresentation','other')
    def test_nonrender_job_does_not_need_render(self):
        with patch.object(m.c,'protected') as reader:m.render_gate('inbox','candidate');reader.assert_not_called()
    def test_probe_compiles_and_credentials_not_given_to_node(self):
        for target in m.TARGETS:
            code=m.probe_source(target,self.expected);compile(code,'<probe>','exec')
            self.assertIn("env={'PATH':",code);self.assertIn("'node':node_version",code)
            if target.startswith('inbox'):self.assertIn("compile(INLINE_SOURCE",code)
    def drift(self,change=None,allowed=False):
        baseline={s['job']:{'type':'job','invariant':'fixed','images':[m.source_image(t)]} for t,s in m.TARGETS.items()};baseline['website']={'type':'app','invariant':'fixed','images':['original']};current=copy.deepcopy(baseline)
        if change:change(current)
        def verify(folder):return None,None,{'registry':'test','repository':'app','digest':folder.name.removesuffix('-security-v1')}
        with patch.object(m.c,'protected',return_value={'bindings':baseline}),patch.object(m.base,'current_resources',return_value=[]),patch.object(m.base,'bindings',return_value=current),patch.object(m.scanner,'verify',side_effect=verify):
            if allowed:m.bindings_gate()
            else:
                with self.assertRaises(ValueError):m.bindings_gate()
    def test_unchanged_inventory(self):self.drift(allowed=True)
    def test_known_image_allowed(self):self.drift(lambda x:x['ibkrv2stg-inbox-acceptance-r2'].update(images=['test/app@inbox']),True)
    def test_external_image_rejected(self):self.drift(lambda x:x['website'].update(images=['other']))
    def test_configuration_drift_rejected(self):self.drift(lambda x:x['ibkrv2stg-inbox-acceptance-r2'].update(invariant='other'))
    def test_inventory_drift_rejected(self):self.drift(lambda x:x.update(unexpected={}))


if __name__=='__main__':unittest.main()
