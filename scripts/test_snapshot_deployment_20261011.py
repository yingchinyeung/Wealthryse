import copy
import unittest
import deploy_snapshot_correction_20261011 as d

class SnapshotDeploymentTests(unittest.TestCase):
    def job(self):return {'name':d.JOB,'id':d.PATH,'properties':{'configuration':{'triggerType':'Manual','secrets':[{'name':'database','keyVaultUrl':'https://example.invalid/ref'}]},'template':{'containers':[{'image':'old','command':['python'],'args':['scripts/freeze_month_end_snapshot.py'],'env':[{'name':'DATABASE_URL','secretRef':'database'}]}]}}}
    def proofs(self):
        p={'passed':True,'image':'candidate','source_hashes':{'test':'abc'},'subjects':[{'subject':s,'passed':True,'quality_status':'warn','payload_sha256':'a'*64,'sections_sha256':{str(n):'b'*64 for n in range(6)}} for s in sorted(d.qa.SUBJECTS)],'database_writes':0,'production_connection_used':False,'source_overlays':False,'private_fixture_uploaded':False,'unchanged_replays':5,'snapshot_history_preserved':True}
        candidate={'passed':True,'image':'candidate','proof':p};reference=copy.deepcopy(candidate);reference['image']='reference';reference['proof']['image']='reference'
        return reference,candidate
    def test_valid(self):d.scope(self.job());d.parity(*self.proofs(),'candidate',{'test':'abc'})
    def test_wrong_target(self):
        j=self.job();j['name']='another'
        with self.assertRaises(ValueError):d.scope(j)
    def test_wrong_command(self):
        j=self.job();j['properties']['template']['containers'][0]['args']=['other.py']
        with self.assertRaises(ValueError):d.scope(j)
    def test_wrong_trigger(self):
        j=self.job();j['properties']['configuration']['triggerType']='Schedule'
        with self.assertRaises(ValueError):d.scope(j)
    def test_secret(self):
        j=self.job();j['properties']['configuration']['secrets'][0]['value']='synthetic'
        with self.assertRaises(ValueError):d.scope(j)
    def test_unrelated(self):
        with self.assertRaises(ValueError):d.only_target({d.JOB:2,'other':2},{d.JOB:1,'other':1})
        d.only_target({d.JOB:2,'other':1},{d.JOB:1,'other':1})
    def test_parity_mismatch(self):
        r,c=self.proofs();c['proof']['subjects'][0]['payload_sha256']='c'*64
        with self.assertRaises(ValueError):d.parity(r,c,'candidate',{'test':'abc'})
    def test_missing_subject(self):
        r,c=self.proofs();c['proof']['subjects'].pop()
        with self.assertRaises(ValueError):d.parity(r,c,'candidate',{'test':'abc'})
    def test_boolean_count(self):
        r,c=self.proofs();c['proof']['database_writes']=False
        with self.assertRaises(ValueError):d.parity(r,c,'candidate',{'test':'abc'})
    def test_production_or_write(self):
        for key,value in (('production_connection_used',True),('database_writes',1),('source_overlays',True),('private_fixture_uploaded',True)):
            r,c=self.proofs();c['proof'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):d.parity(r,c,'candidate',{'test':'abc'})
    def test_wrong_image(self):
        r,c=self.proofs()
        with self.assertRaises(ValueError):d.parity(r,c,'other',{'test':'abc'})
    def test_patch_roundtrip(self):
        j=self.job();applied=copy.deepcopy(j);applied['properties']['template']=d.patch(j,'new')['properties']['template']
        self.assertEqual(d.patch(applied,'old')['properties']['template'],j['properties']['template'])
        self.assertEqual(set(d.patch(j,'new')['properties']),{'template'})

if __name__=='__main__':unittest.main()
