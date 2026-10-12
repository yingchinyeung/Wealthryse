import copy,unittest
from deploy_report_handover_20261011 import accept,release,unrelated,body,SUBJECTS
class Tests(unittest.TestCase):
    def setUp(self):
        self.p={'passed':True,'database_writes':0,'identity':{'role':'ibkr_stg_web','ro':'on','ssl':True},'image':'new','source_hashes':{'a':'b'},'subjects':[{'subject':s,'months':21,'released_ids':[1],'passed':True} for s in SUBJECTS]}
        self.old=copy.deepcopy(self.p);self.old['image']=release.BASE
    def check(self):accept(self.p,self.old,'new',{'a':'b'})
    def test_valid(self):self.check()
    def test_wrong_source(self):
        self.p['source_hashes']={}
        with self.assertRaises(ValueError):self.check()
    def test_missing_download(self):
        self.p['subjects'][0]['released_ids']=[]
        with self.assertRaises(ValueError):self.check()
    def test_missing_subject(self):
        self.p['subjects'].pop()
        with self.assertRaises(ValueError):self.check()
    def test_database_write(self):
        self.p['database_writes']=1
        with self.assertRaises(ValueError):self.check()
    def test_wrong_role(self):
        self.p['identity']['role']='admin'
        with self.assertRaises(ValueError):self.check()
    def test_unrelated_workload(self):
        with self.assertRaises(ValueError):unrelated({'ibkrv2stg-web':1,'job':2},{'ibkrv2stg-web':2,'job':3})
    def test_patch_only_template(self):
        live={'identity':{'type':'UserAssigned'},'properties':{'environmentId':'env','configuration':{'ingress':{'external':True},'secrets':[]},'template':{'containers':[{'image':'old','env':[]}],'revisionSuffix':'old','scale':{'minReplicas':1}}}}
        original=copy.deepcopy(live);result=body(live,'new','next')
        self.assertEqual(live,original);self.assertEqual(set(result['properties']),{'template'})
        self.assertEqual(result['properties']['template']['scale'],{'minReplicas':1})
if __name__=='__main__':unittest.main()
