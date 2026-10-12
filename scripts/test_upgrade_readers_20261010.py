"""Offline positive/negative reader release guards; never contact Azure."""
import unittest
from control_upgrade_readers_20261010 import accept,execution_env
from deploy_upgrade_readers_20261010 import validate_trial


class Guards(unittest.TestCase):
    def proof(self):return {'status':'passed','messages_sent':0,'database_access':'verified','allowed_relation_checks':9,
        'financial_rows_read':0,'source_rows_written':0,'authority_rows_written':0,'agents_enabled':0}
    def test_observation_exact(self):accept('ibkrv2stg-observe-reader',self.proof())
    def test_observation_side_effects_fail(self):
        for k in ('financial_rows_read','source_rows_written','authority_rows_written','agents_enabled','messages_sent'):
            p=self.proof();p[k]=1
            with self.subTest(k=k),self.assertRaises(ValueError):accept('ibkrv2stg-observe-reader',p)
    def test_observation_incomplete_fails(self):
        p=self.proof();p['allowed_relation_checks']=8
        with self.assertRaises(ValueError):accept('ibkrv2stg-observe-reader',p)
    def test_audit_append_only_required(self):
        p={'status':'passed','messages_sent':0,'database_access':'verified','reachable_roles_verified':True,'source_mutation_authority':False,'ledger_append_only':True,'events_written':0,'source_table_access':False,'mode':'verify'}
        accept('ibkrv2stg-agent-audit',p)
        for k,v in (('ledger_append_only',False),('events_written',1),('source_table_access',True)):
            altered=dict(p);altered[k]=v
            with self.subTest(k=k),self.assertRaises(ValueError):accept('ibkrv2stg-agent-audit',altered)
    def test_authority_no_enrollment_required(self):
        p={'status':'passed','messages_sent':0,'database_access':'verified','authority_source_checked':True,'unregistered_principal_denied':True,
           'tenant_authority_rows':{'controls':0,'grants':0},'authority_rows_written':0,'agents_enabled':0,'source_table_access':False}
        accept('ibkrv2stg-authority',p);p['unregistered_principal_denied']=False
        with self.assertRaises(ValueError):accept('ibkrv2stg-authority',p)
    def test_image_payload_and_saved_settings_required(self):
        p={'passed':True,'image':'digest','payload_sha256':'payload','settings_preserved':True,'proof':self.proof()}
        validate_trial('ibkrv2stg-observe-reader',p,'digest','payload')
        for k,v in (('passed',False),('image','other'),('payload_sha256','other'),('settings_preserved',False)):
            altered=dict(p);altered[k]=v
            with self.subTest(k=k),self.assertRaises(ValueError):validate_trial('ibkrv2stg-observe-reader',altered,'digest','payload')
    def test_exact_execution_alias_only(self):
        b=[{'name':'READER_URL','secretRef':'reader-url'}]
        a=[{'name':'READER_URL','secretRef':'cappjob-reader','value':''}]
        execution_env('reader',a,b)
        for value in ({'name':'OTHER','secretRef':'cappjob-reader'}, {'name':'READER_URL','secretRef':'unknown'}, {'name':'READER_URL','secretRef':'cappjob-reader','value':'synthetic-not-allowed'}):
            with self.assertRaises(ValueError):execution_env('reader',[value],b)


if __name__=='__main__':unittest.main()
