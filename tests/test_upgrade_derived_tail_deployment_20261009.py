import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from deploy_upgrade_derived_tail_20261009 import accept,patch
from control_upgrade_legacy_failover_probe_20261009 import execution_matches

class Gates(unittest.TestCase):
    def setUp(self):
        self.proof={'passed':True,'image':'candidate','job_settings_preserved':True,'proof':{'passed':True,'live_database_writes':0,'clone_public_table_differences':[],'public_tables_checked':21,'session_temporary_rows_removed':4,'session_temporary_tables_dropped':True,'records':[{'subject':s,'approved_pair_refreshed':True,'onboarding_activated':False} for s in ('alpha-world-ms','alpha-world-t')]}}
    def test_pass(self):accept(self.proof,'candidate')
    def test_image(self):
        with self.assertRaises(ValueError):accept(self.proof,'other')
    def test_public_change(self):
        self.proof['proof']['clone_public_table_differences']=['nav_snapshots']
        with self.assertRaises(ValueError):accept(self.proof,'candidate')
    def test_one_subject(self):
        self.proof['proof']['records'].pop()
        with self.assertRaises(ValueError):accept(self.proof,'candidate')
    def test_activation(self):
        self.proof['proof']['records'][0]['onboarding_activated']=True
        with self.assertRaises(ValueError):accept(self.proof,'candidate')
    def test_patch_preserves_source(self):
        job={'properties':{'template':{'containers':[{'image':'old','env':[{'name':'DATABASE_URL','secretRef':'database-url','value':''}],'args':['unchanged']}],'volumes':None}}};before=copy.deepcopy(job)
        body=patch(job,'candidate')
        self.assertEqual(job,before);self.assertEqual(body['properties']['template']['containers'][0]['args'],['unchanged']);self.assertEqual(body['properties']['template']['containers'][0]['image'],'candidate')
    def test_azure_alias(self):
        w={'image':'i','command':['python'],'args':['exact'],'env':[{'name':'DATABASE_URL','secretRef':'database-url'},{'name':'PORTFOLIO_TENANT_ID','value':'tenant'}]};a=copy.deepcopy(w);a['env'][0]['secretRef']='cappjob-ibkr-v11-daily'
        self.assertTrue(execution_matches(a,w));a['env'][1]['value']='wrong';self.assertFalse(execution_matches(a,w))
    def test_unknown_alias(self):
        w={'env':[{'name':'DATABASE_URL','secretRef':'database-url'}]};a={'env':[{'name':'DATABASE_URL','secretRef':'unknown'}]}
        self.assertFalse(execution_matches(a,w))

if __name__=='__main__':unittest.main()
