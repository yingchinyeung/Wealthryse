import copy,unittest,uuid
from ibkr_etl.util import canonical_json
from test_august_monthly_reporting_20261011 import fixture as base
from reconcile_august_monthly_reporting_20261011 import digest,correct
from prepare_august_snapshot_correction_20261011 import prepare
def fixture():
    p,f=base();row={'snapshot_id':uuid.uuid4(),'tenant_id':uuid.uuid4(),'subject_id':p['subject_id'],'period_end':p['period_end'],'reporting_currency':'USD',
        'snapshot_payload_json':canonical_json(p),'data_sha256':digest(p),'status':'approved','created_at':'old','approved_at':'old','approved_by':'owner',
        'source_manifest_json':'{"original":true}','publication_allowed':False}
    plan={'source_snapshot_id':str(row['snapshot_id']),'source_sha256':row['data_sha256'],'status':row['status'],'candidate_sha256':digest(correct(p,f)),
        'calculation_sha256':digest(f),'candidate_verified':True,'monthly_return_nav_cash_unchanged':True}
    return row,plan,f
class AugustVersionTests(unittest.TestCase):
    def test_original_and_approvals_preserved(self):
        r,p,f=fixture();old=copy.deepcopy(r);n=prepare(r,p,f,'new');self.assertEqual(r,old);self.assertIsNone(n['approved_by']);self.assertIsNone(n['approved_at']);self.assertEqual(n['status'],'draft');self.assertFalse(n['publication_allowed'])
    def test_identity_deterministic(self):
        r,p,f=fixture();self.assertEqual(prepare(r,p,f,'a')['snapshot_id'],prepare(r,p,f,'b')['snapshot_id'])
    def reject(self,change):
        r,p,f=fixture();change(r,p,f)
        with self.assertRaises(ValueError):prepare(r,p,f,'now')
    def test_wrong_subject(self):self.reject(lambda r,p,f:r.update(subject_id='alpha-world-t'))
    def test_wrong_source(self):self.reject(lambda r,p,f:p.update(source_sha256='bad'))
    def test_wrong_status(self):self.reject(lambda r,p,f:r.update(status='superseded'))
    def test_wrong_calculation(self):self.reject(lambda r,p,f:p.update(calculation_sha256='bad'))
    def test_wrong_candidate(self):self.reject(lambda r,p,f:p.update(candidate_sha256='bad'))
    def test_unqualified_plan(self):self.reject(lambda r,p,f:p.update(candidate_verified=False))
if __name__=='__main__':unittest.main()
