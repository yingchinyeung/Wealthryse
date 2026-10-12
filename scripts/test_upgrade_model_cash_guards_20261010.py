import calendar,copy,unittest
from unittest.mock import patch
import control_upgrade_model_cash_runtime_20261010 as m
import deploy_upgrade_model_cash_20261010 as d


class Guards(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(m,'files',return_value={'web':'hash'}))
        self.proof={'passed':True,'mode':'candidate','tests':42,'source_files':{'web':'hash'},'readiness_statuses':[200,503,200],
            'verified_tls':True,'database_readonly':True,'database_role':'ibkr_stg_web','database_writes':0,'reports_changed':False,
            'checked_model_months':[{'subject':s,'requested_month':f'{y}-{n:02d}-{calendar.monthrange(y,n)[1]}','passed':True} for s in ('alpha-world-ms','alpha-world-t') for y,months in ((2025,range(1,13)),(2026,range(1,10))) for n in months]}
        for row in self.proof['checked_model_months']:row['as_of']=row['requested_month']
    def reject(self,**changes):
        with self.assertRaises(ValueError):m.accept({**self.proof,**changes},'candidate')
    def test_valid(self):m.accept(self.proof,'candidate')
    def test_incomplete_months(self):self.reject(checked_model_months=self.proof['checked_model_months'][:-1])
    def test_duplicate_month(self):self.reject(checked_model_months=[self.proof['checked_model_months'][0]]*42)
    def test_wrong_role(self):self.reject(database_role='owner')
    def test_no_tls(self):self.reject(verified_tls=False)
    def test_writable(self):self.reject(database_readonly=False)
    def test_write(self):self.reject(database_writes=1)
    def test_boolean_not_zero(self):self.reject(database_writes=False)
    def test_failed_readiness(self):self.reject(readiness_statuses=[200,200,200])
    def test_wrong_source(self):self.reject(source_files={'web':'different'})
    def test_report_change(self):self.reject(reports_changed=True)
    def test_fallback(self):m.accept({**self.proof,'mode':'fallback','tests':14,'checked_model_months':[]},'fallback')
    def test_lossless_telemetry_dates(self):
        for row in self.proof['checked_model_months']:
            for key in ('requested_month','as_of'):row[key]+='T00:00:00.0000000Z'
        m.accept(self.proof,'candidate')
    def test_non_midnight_telemetry_rejected(self):
        self.proof['checked_model_months'][0]['requested_month']+='T01:00:00Z'
        self.reject()
    def web(self):return {'identity':{'type':'UserAssigned'},'properties':{'environmentId':'env','configuration':{'ingress':{'external':True,'traffic':[{'weight':100,'latestRevision':True}]},'secrets':[{'name':'db','keyVaultUrl':'https://example.vault.azure.net/secrets/db'}]},'template':{'revisionSuffix':'old','containers':[{'image':'old','env':[{'name':'DATABASE_URL','secretRef':'db','value':''}],'probes':[{'type':'Readiness','httpGet':{'path':'/readyz'}}]}],'scale':{'minReplicas':1}}}}
    def test_image_and_suffix_only(self):
        live=self.web();result=d.body(live,'new','revision');self.assertEqual(set(result['properties']),{'template'});self.assertEqual(result['properties']['template']['containers'][0]['image'],'new');self.assertEqual(live['properties']['template']['containers'][0]['image'],'old')
    def test_probes_and_scale_preserved(self):
        live=self.web();template=d.body(live,'new','revision')['properties']['template'];self.assertEqual(template['scale'],live['properties']['template']['scale']);self.assertEqual(template['containers'][0]['probes'],live['properties']['template']['containers'][0]['probes'])
    def test_settings_and_credentials_not_patched(self):
        result=d.body(self.web(),'new','revision');self.assertNotIn('configuration',result['properties']);self.assertNotIn('identity',result);self.assertEqual(result['properties']['template']['containers'][0]['env'],[{'name':'DATABASE_URL','secretRef':'db'}])
    def test_extra_container_rejected(self):
        live=self.web();live['properties']['template']['containers'].append({'image':'extra'})
        with self.assertRaises(ValueError):d.body(live,'new','revision')


if __name__=='__main__':unittest.main()
