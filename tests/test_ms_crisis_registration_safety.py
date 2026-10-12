"""Pure safety checks; no imports that access Azure or the database."""
import ast,copy,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
source=(ROOT/'scripts/register_ms_crisis_runtime.py').read_text(encoding='utf-8')
space={'copy':copy}
exec(compile(ast.Module(body=[n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name in ('require','verify_state')],type_ignores=[]),'registration-safety','exec'),space)
class RegistrationSafetyTests(unittest.TestCase):
    def fixture(self):
        before={'approvals':[{'id':'keep'}],'batches':[{'status':'approved'}],'snapshots':[],'runs':[{'generation_run_id':1,'snapshot_id':'keep','output_sha256':'keep'}]}
        artifacts=[]
        for i in range(21):
            before['snapshots'].append({'snapshot_id':f'old{i}','status':'frozen','data_sha256':f'oldhash{i}'})
            artifacts.append({'original_snapshot_id':f'old{i}','snapshot_id':f'new{i}','period_end':str(i),'data_sha256':f'hash{i}','factsheet_sha256':f'pdf{i}','presentation_sha256':f'ppt{i}'})
        after=copy.deepcopy(before)
        for r in after['snapshots']:r['status']='superseded'
        for i,a in enumerate(artifacts):
            after['snapshots'].append({'snapshot_id':a['snapshot_id'],'status':'frozen','publication_allowed':False,'reporting_currency':'HKD','period_end':str(i),'data_sha256':a['data_sha256']})
            for offset,kind in enumerate(('factsheet','presentation')):
                after['runs'].append({'generation_run_id':2+i*2+offset,'snapshot_id':a['snapshot_id'],'status':'awaiting_approval','report_type':kind,'output_sha256':a[kind+'_sha256']})
        return before,after,artifacts
    def test_only_qualified_private_version_delta_allowed(self):space['verify_state'](*self.fixture())
    def test_old_approval_payload_run_or_new_release_changes_rejected(self):
        for issue in ('approval','old_payload','old_run','new_status','new_currency','new_hash','approved_old'):
            before,after,artifacts=self.fixture()
            if issue=='approval':after['approvals'][0]['id']='changed'
            if issue=='old_payload':after['snapshots'][0]['data_sha256']='changed'
            if issue=='old_run':after['runs'][0]['output_sha256']='changed'
            if issue=='new_status':after['runs'][-1]['status']='released_internal'
            if issue=='new_currency':after['snapshots'][-1]['reporting_currency']='USD'
            if issue=='new_hash':after['runs'][-1]['output_sha256']='changed'
            if issue=='approved_old':before['snapshots'][0]['status']='approved'
            with self.subTest(issue=issue),self.assertRaises(ValueError):space['verify_state'](before,after,artifacts)
    def test_no_source_or_approval_write_statement(self):
        import re
        tables=set(re.findall(r'(?:INSERT INTO|UPDATE|DELETE FROM)\s+([a-z_]+)',source))
        self.assertEqual(tables,{'reporting_snapshots','report_generation_runs','audit_events'})
        self.assertNotIn("SET status='approved'",source)
        self.assertIn("if mode=='register':raw.commit()",source)
        self.assertIn("raw.rollback();raw.execute",source)
if __name__=='__main__':unittest.main()
