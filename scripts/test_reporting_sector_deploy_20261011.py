import copy,unittest
from deploy_reporting_sector_web_20261011 import accept,unrelated,body,SUBJECTS

class Tests(unittest.TestCase):
    def setUp(self):
        self.p={'passed':True,'image':'candidate','source_hashes':{'a':'b'},'subjects':[{'subject':s,'passed':True} for s in SUBJECTS], 'database_writes':0,'production_connection_used':False,'source_overlays':False,'broker_identity_evidence_verified':True,
            'reconciled_aliases':[{'conid':'804144296','broker_master_symbol':'SKYD','frozen_report_symbol':'PSKY','predecessor_price_continuity_asserted':False}]}
    def check(self):accept(self.p,'candidate',{'a':'b'})
    def test_valid(self):self.check()
    def test_wrong_image(self):
        self.p['image']='wrong'
        with self.assertRaises(ValueError):self.check()
    def test_missing_subject(self):
        self.p['subjects'].pop()
        with self.assertRaises(ValueError):self.check()
    def test_failed_subject(self):
        self.p['subjects'][0]['passed']=False
        with self.assertRaises(ValueError):self.check()
    def test_write(self):
        self.p['database_writes']=1
        with self.assertRaises(ValueError):self.check()
    def test_boolean_write(self):
        self.p['database_writes']=False
        with self.assertRaises(ValueError):self.check()
    def test_no_identity_proof(self):
        self.p['broker_identity_evidence_verified']=False
        with self.assertRaises(ValueError):self.check()
    def test_other_alias(self):
        self.p['reconciled_aliases'][0]['conid']='OTHER'
        with self.assertRaises(ValueError):self.check()
    def test_predecessor_claim(self):
        self.p['reconciled_aliases'][0]['predecessor_price_continuity_asserted']=True
        with self.assertRaises(ValueError):self.check()
    def test_unrelated_drift(self):
        with self.assertRaises(ValueError):unrelated({'ibkrv2stg-web':{},'job':1},{'ibkrv2stg-web':{},'job':2})
    def test_website_only(self):unrelated({'ibkrv2stg-web':1,'job':2},{'ibkrv2stg-web':3,'job':2})
    def test_body_preserves_settings(self):
        live={'identity':{'type':'UserAssigned'},'properties':{'environmentId':'env','configuration':{'ingress':{'external':True},'secrets':[]},'template':{'revisionSuffix':'old','containers':[{'image':'old','env':[{'name':'DB','secretRef':'db','value':''}],'probes':[]}],'scale':{'minReplicas':1}}}}
        original=copy.deepcopy(live);result=body(live,'new','new')
        self.assertEqual(live,original);self.assertEqual(set(result['properties']),{'template'})
        self.assertEqual(result['properties']['template']['scale'],original['properties']['template']['scale'])
        self.assertEqual(result['properties']['template']['containers'][0]['env'],[{'name':'DB','secretRef':'db'}])

if __name__=='__main__':unittest.main()
