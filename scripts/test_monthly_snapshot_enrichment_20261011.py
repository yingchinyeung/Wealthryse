import copy,json,unittest,uuid
from test_legacy_monthly_reporting_20261011 import fixture as payload_fixture
from reconcile_legacy_monthly_reporting_20261011 import enrich
from ibkr_etl.util import canonical_json
import prepare_monthly_snapshot_enrichment_20261011 as c
def fixture():
    p,f=payload_fixture();raw=canonical_json(p)
    row={'snapshot_id':uuid.uuid4(),'tenant_id':uuid.UUID('00000000-0000-0000-0000-000000000001'),'subject_id':p['subject_id'],'period_end':p['period_end'],
         'reporting_currency':'USD','status':'approved','snapshot_payload_json':raw,'data_sha256':c.sha(raw),'created_at':'old','approved_at':'old','approved_by':'owner',
         'source_manifest_json':'{"reporting_correction":{"preserved":true}}','publication_allowed':False,'quality_status':'pass','formula_version':'unchanged'}
    plan={'source_snapshot_id':str(row['snapshot_id']),'period_end':p['period_end'],'subject':p['subject_id'],'source_sha256':row['data_sha256'],
          'calculation_sha256':c.sha(canonical_json(f)),'candidate_sha256':c.sha(canonical_json(enrich(p,f))),'monthly_reconciliation_passed':True,'frozen_nav_risk_returns_unchanged':True}
    return row,plan,f
class VersionedMonthlyTests(unittest.TestCase):
    def test_new_version_preserves_original_and_approvals(self):
        r,p,f=fixture();before=copy.deepcopy(r);new=c.prepare(r,p,f,'now')
        self.assertEqual(r,before);self.assertEqual(new['status'],'draft');self.assertIsNone(new['approved_at']);self.assertIsNone(new['approved_by'])
        self.assertNotEqual(new['snapshot_id'],r['snapshot_id']);self.assertEqual(new['publication_allowed'],False)
    def test_unrelated_fields_and_existing_provenance_preserved(self):
        r,p,f=fixture();new=c.prepare(r,p,f,'now')
        for k in ('quality_status','formula_version','tenant_id','subject_id','period_end'):self.assertEqual(new[k],r[k])
        self.assertEqual(json.loads(new['source_manifest_json'])['reporting_correction'],{'preserved':True})
    def test_identity_is_deterministic(self):
        r,p,f=fixture();self.assertEqual(c.prepare(r,p,f,'one')['snapshot_id'],c.prepare(r,p,f,'two')['snapshot_id'])
    def reject(self,change):
        r,p,f=fixture();change(r,p,f)
        with self.assertRaises(ValueError):c.prepare(r,p,f,'now')
    def test_wrong_subject(self):self.reject(lambda r,p,f:r.update(subject_id='alpha-world-t'))
    def test_inactive_source(self):self.reject(lambda r,p,f:r.update(status='superseded'))
    def test_changed_identity(self):self.reject(lambda r,p,f:p.update(source_snapshot_id=str(uuid.uuid4())))
    def test_changed_payload(self):self.reject(lambda r,p,f:r.update(snapshot_payload_json='{}'))
    def test_changed_calculation(self):self.reject(lambda r,p,f:p.update(calculation_sha256='bad'))
    def test_changed_candidate(self):self.reject(lambda r,p,f:p.update(candidate_sha256='bad'))
    def test_unqualified_plan(self):self.reject(lambda r,p,f:p.update(monthly_reconciliation_passed=False))
if __name__=='__main__':unittest.main()
