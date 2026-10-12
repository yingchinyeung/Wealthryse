import copy,unittest
import deploy_reporting_onboard_20261011 as d
import test_reporting_onboard_qa_20261011 as qa_tests

class DeploymentGuards(unittest.TestCase):
    def job(self):return {'name':d.JOB,'id':d.PATH,'properties':{'configuration':{'triggerType':'Manual','secrets':[{'name':'database','keyVaultUrl':'https://example.invalid/ref'}]},'template':{'containers':[{'image':'old','command':['python'],'args':['scripts/run_self_service_onboarding_reports.py'],'env':[{'name':'DATABASE_URL','secretRef':'database'}]}]}}}
    def acceptance(self):return ({'passed':True,'image':'candidate','proofs':qa_tests.Guards().proof()},{'passed':True,'image':'candidate','pdfs_byte_identical':10,'presentation_packages_compared':10,'slides_compared':200,'financial_charts_verified':60,'artifacts':[{'content_identical':True} for _ in range(20)]})
    def test_valid(self):d.scope(self.job());d.accept(*self.acceptance(),'candidate')
    def test_target(self):
        j=self.job();j['name']='another'
        with self.assertRaises(ValueError):d.scope(j)
    def test_command(self):
        j=self.job();j['properties']['template']['containers'][0]['args']=['different.py']
        with self.assertRaises(ValueError):d.scope(j)
    def test_trigger(self):
        j=self.job();j['properties']['configuration']['triggerType']='Schedule'
        with self.assertRaises(ValueError):d.scope(j)
    def test_inline_secret(self):
        j=self.job();j['properties']['configuration']['secrets'][0]['value']='dummy'
        with self.assertRaises(ValueError):d.scope(j)
    def test_unrelated_drift(self):
        with self.assertRaises(ValueError):d.only_target({d.JOB:2,'other':2},{d.JOB:1,'other':1})
        d.only_target({d.JOB:2,'other':1},{d.JOB:1,'other':1})
    def test_output_image(self):
        r,o=self.acceptance();o['image']='wrong'
        with self.assertRaises(ValueError):d.accept(r,o,'candidate')
    def test_missing_output(self):
        r,o=self.acceptance();o['artifacts'].pop()
        with self.assertRaises(ValueError):d.accept(r,o,'candidate')
    def test_patch_inverse(self):
        j=self.job();applied=copy.deepcopy(j);applied['properties']['template']=d.patch(j,'new')['properties']['template']
        restored=d.patch(applied,'old')['properties']['template']
        self.assertEqual(restored,j['properties']['template'])
        self.assertEqual(set(d.patch(j,'new')),{'properties'})
        self.assertEqual(set(d.patch(j,'new')['properties']),{'template'})

if __name__=='__main__':unittest.main()
