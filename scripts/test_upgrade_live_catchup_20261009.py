import copy,json,unittest
from pathlib import Path
from control_upgrade_live_catchup_20261009 import risk_code,normal_template

class LiveCatchupTests(unittest.TestCase):
    def intent(self):
        path=Path(__file__).resolve().parents[1]/'.work/platform-upgrade-20261009-stage8/clone-risk-candidate-history-all/intent.json'
        return json.loads(path.read_bytes())
    def test_qualified_bounded_loop_and_normal_worker_retained(self):
        code=risk_code(self.intent());compile(code,'qualified-live-catchup','exec')
        for required in ('for wave in range(32)','time.monotonic()-started>2900','audit_limit=100,rebuild_limit=100',"if len(seen)!=total:raise ValueError",'scripts/build_risk_snapshots.py',"'live_cache_refresh':True"):
            self.assertIn(required,code)
    def test_live_host_and_tenant_guards(self):
        code=risk_code(self.intent())
        self.assertIn('Unexpected live database host',code)
        self.assertIn('Unexpected secondary database',code)
        self.assertIn('Unexpected tenant',code)
        self.assertNotIn('ibkrv2stg-upgrade-recovery-1009',code)
    def test_no_false_zero_live_write_claim(self):
        code=risk_code(self.intent());self.assertNotIn("'live_database_writes':0",code)
        self.assertNotIn('UPGRADE_CLONE_CYCLE',code)
    def test_tampered_qualification_source_rejected(self):
        intent=self.intent();intent['source_sha256']='0'*64
        with self.assertRaises(ValueError):risk_code(intent)
    def test_unknown_encoding_rejected(self):
        intent=self.intent();intent['template']['containers'][0]['args'][1]='print(1)'
        with self.assertRaises(ValueError):risk_code(intent)
    def test_normal_start_omits_optional_null_fields_only(self):
        job={'properties':{'template':{'containers':[{'command':['python'],'args':['worker.py'],'env':[{'name':'X','value':''}]}],'initContainers':None,'volumes':None}}}
        before=copy.deepcopy(job);result=normal_template(job)
        self.assertEqual(job,before)
        self.assertEqual(result,{'containers':before['properties']['template']['containers']})
    def test_extra_container_rejected(self):
        with self.assertRaises(ValueError):normal_template({'properties':{'template':{'containers':[{},{}]}}})

if __name__=='__main__':unittest.main()
