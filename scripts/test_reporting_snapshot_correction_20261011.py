import copy,json,unittest,uuid
from unittest.mock import Mock
from ibkr_etl.util import canonical_json
import prepare_reporting_snapshot_correction_20261011 as c

def fixture():
    p={'subject_id':'U16088606','period_end':'2026-09-30','reporting_currency':'USD','data_as_of':'2026-09-30',
       'holdings':{'holdings':[]},'sector_allocation':{'classifications':[]},'source_citations':{'holdings':[],'sector_allocation':[]},
       'nav':[1,2],'risk':{'v':3},'returns':[4]}
    raw=canonical_json(p)
    row={'snapshot_id':uuid.uuid4(),'tenant_id':uuid.UUID('00000000-0000-0000-0000-000000000001'),'subject_id':p['subject_id'],'period_end':p['period_end'],
         'status':'approved','snapshot_payload_json':raw,'data_sha256':c.sha(raw),'reporting_currency':'USD','source_manifest_json':'{"sources":{}}',
         'created_at':'old','approved_at':'old-approval','approved_by':'owner','publication_allowed':False,'quality_status':'pass','formula_version':'original'}
    output={'holdings':{'data':{'holdings':[],'valuation_method':'dated-reporting-fx-v1'},'citations':[{'method':'FX'}]},
            'sector_allocation':{'data':{'classifications':[],'valuation_method':'dated-reporting-fx-v1'},'citations':[]}}
    changed=copy.deepcopy(p)
    for key in output:changed[key]=output[key]['data'];changed['source_citations'][key]=output[key]['citations']
    expected={'snapshot_id':str(row['snapshot_id']),'subject':row['subject_id'],'period_end':row['period_end'],'status':row['status'],
              'source_sha256':row['data_sha256'],'candidate_sha256':c.sha(canonical_json(changed)),'needs_snapshot_correction':True,'nav_returns_risk_unchanged':True}
    return row,expected,Mock(return_value=output)

class CorrectionTests(unittest.TestCase):
    def call(self,r,e,n):return c.prepare(r,e,None,n,Mock(side_effect=AssertionError('Unexpected legacy lookup')),'new-time')
    def test_new_draft_does_not_transfer_approvals(self):
        r,e,n=fixture();before=copy.deepcopy(r);result=self.call(r,e,n)
        self.assertEqual(r,before);self.assertEqual(result['status'],'draft');self.assertIsNone(result['approved_at']);self.assertIsNone(result['approved_by'])
        self.assertEqual(result['publication_allowed'],r['publication_allowed']);self.assertNotEqual(result['snapshot_id'],r['snapshot_id'])
    def test_deterministic_identity(self):
        r,e,n=fixture();self.assertEqual(self.call(r,e,n)['snapshot_id'],self.call(r,e,n)['snapshot_id'])
    def test_manifest_tracks_new_evidence_original_version(self):
        r,e,n=fixture();result=self.call(r,e,n);m=json.loads(result['source_manifest_json'])
        self.assertEqual(m['sources'],json.loads(result['snapshot_payload_json'])['source_citations'])
        self.assertEqual(m['reporting_correction']['source_sha256'],r['data_sha256']);self.assertFalse(m['reporting_correction']['prior_approvals_transferred'])
    def reject(self,edit):
        r,e,n=fixture();edit(r,e,n)
        with self.assertRaises(ValueError):self.call(r,e,n)
    def test_composite_rejected(self):self.reject(lambda r,e,n:r.update(subject_id='alpha-world-t'))
    def test_draft_source_rejected(self):self.reject(lambda r,e,n:r.update(status='draft'))
    def test_superseded_source_rejected(self):self.reject(lambda r,e,n:r.update(status='superseded'))
    def test_status_drift(self):self.reject(lambda r,e,n:e.update(status='frozen'))
    def test_wrong_identity(self):self.reject(lambda r,e,n:e.update(snapshot_id=str(uuid.uuid4())))
    def test_wrong_period(self):self.reject(lambda r,e,n:e.update(period_end='2026-08-31'))
    def test_wrong_source_hash(self):self.reject(lambda r,e,n:e.update(source_sha256='bad'))
    def test_bad_frozen_bytes(self):self.reject(lambda r,e,n:r.update(snapshot_payload_json='{}'))
    def test_unqualified_candidate(self):self.reject(lambda r,e,n:e.update(needs_snapshot_correction=False))
    def test_blocked_candidate(self):self.reject(lambda r,e,n:e.update(blocker='fail'))
    def test_missing_unchanged_assurance(self):self.reject(lambda r,e,n:e.update(nav_returns_risk_unchanged=False))
    def test_wrong_candidate_hash(self):self.reject(lambda r,e,n:e.update(candidate_sha256='bad'))
    def test_payload_identity(self):self.reject(lambda r,e,n:r.update(reporting_currency='HKD'))
    def test_unexpected_transform_rejected(self):self.reject(lambda r,e,n:n.return_value['holdings']['data'].update(extra='unplanned'))
    def test_nav_returns_risk_unchanged(self):
        r,e,n=fixture();p=json.loads(self.call(r,e,n)['snapshot_payload_json']);old=json.loads(r['snapshot_payload_json'])
        for key in ('nav','risk','returns'):self.assertEqual(p[key],old[key])
if __name__=='__main__':unittest.main()
