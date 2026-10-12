import copy
import json
from pathlib import Path
import unittest

from prepare_t_hkd_correction_20261009 import prepare, sha, NEW_HASH

ROOT=Path(__file__).resolve().parents[1]


class CorrectionPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.record=json.loads((ROOT/'.work/alpha-t-contract-probe-20261009-v1/contract-result.json').read_bytes())['records'][0]

    def test_only_currency_version_and_obsolete_approval_change(self):
        original=copy.deepcopy(self.record)
        p=prepare(self.record,now='2026-10-09T05:00:00+00:00')
        self.assertEqual(self.record,original)
        b,a=p['before'],p['after']
        self.assertEqual(a['platform_subjects'][0]['lifecycle_status'],'onboarding')
        self.assertEqual(a['platform_subjects'][0]['base_currency'],'HKD')
        self.assertEqual(a['account_onboarding_cases'][0]['stage'],'financial_contract')
        self.assertEqual(len(a['subject_calculation_definitions']),2)
        old,new=a['subject_calculation_definitions']
        self.assertEqual(old['configuration_json'],b['subject_calculation_definitions'][0]['configuration_json'])
        self.assertEqual(old['approval_status'],'superseded')
        self.assertEqual(new['configuration_sha256'],NEW_HASH)
        self.assertEqual(new['approval_status'],'validated')
        self.assertIsNone(new['approved_at']);self.assertIsNone(new['approved_by_identity_sha256'])
        for table in ('derived_portfolios','subject_dependencies','subject_feature_states'):
            self.assertEqual(a[table],b[table])
        for old,new in zip(b['account_onboarding_approvals'],a['account_onboarding_approvals']):
            if old['approval_type']=='external_market_data':self.assertEqual(new,old)
            else:
                self.assertEqual(new['approval_status'],'revoked')
                self.assertEqual(new['scope_json'],old['scope_json'])
                self.assertEqual(new['scope_sha256'],old['scope_sha256'])
        for old,new in zip(b['account_onboarding_checks'],a['account_onboarding_checks']):
            if old['check_id'] in ('calculation_definition_approved','financial_contract_approved'):
                self.assertEqual(new['status'],'pending')
                self.assertEqual(new['evidence_json']['prior_check_evidence'],old['evidence_json'])
            else:self.assertEqual(new,old)
        for old,new in zip(b['portfolio_benchmark_mapping'],a['portfolio_benchmark_mapping']):
            self.assertEqual(new,{**old,'reporting_currency':'HKD'})

    def test_drift_and_existing_artifacts_fail_closed(self):
        cases=[('platform_subjects','lifecycle_status','active'),
               ('platform_subjects','base_currency','HKD'),
               ('platform_subjects','tenant_id','other'),
               ('account_onboarding_cases','stage','website_preview'),
               ('subject_calculation_definitions','configuration_sha256','0'*64),
               ('subject_calculation_definitions','approved_by_identity_sha256','0'*64),
               ('portfolio_benchmark_mapping','mapping_type','primary')]
        for table,key,value in cases:
            with self.subTest(table=table,key=key):
                r=copy.deepcopy(self.record);r['tables'][table][0][key]=value
                r['tables_sha256']=sha(r['tables'])
                with self.assertRaises(ValueError):prepare(r,now='2026-10-09T05:00:00+00:00')
        for table in self.record['artifact_counts']:
            with self.subTest(artifact=table):
                r=copy.deepcopy(self.record);r['artifact_counts'][table]=1
                with self.assertRaises(ValueError):prepare(r,now='2026-10-09T05:00:00+00:00')

    def test_tampered_evidence_rejected(self):
        r=copy.deepcopy(self.record);r['tables_sha256']='0'*64
        with self.assertRaises(ValueError):prepare(r,now='2026-10-09T05:00:00+00:00')


if __name__=='__main__':unittest.main()
