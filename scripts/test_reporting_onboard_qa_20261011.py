import copy,unittest
import control_reporting_onboard_qa_20261011 as q

class Guards(unittest.TestCase):
    def proof(self):
        return [dict(subject=s,passed=True,months=2,artifacts=4,slides_structurally_checked=40,financial_charts_verified=12,database_writes=0,report_registrations=0,production_connection_used=False,approvals_changed=False,native_visual_review='UNVERIFIED',real_viewer_sign_in='UNVERIFIED',fourteen_day_observation='UNVERIFIED',receipt_blob='candidate-stage36-onboard-v1/'+s+'/receipt.json') for s in q.SUBJECTS]
    def test_valid(self):q.accept(self.proof())
    def test_missing(self):
        with self.assertRaises(ValueError):q.accept(self.proof()[:-1])
    def test_duplicate(self):
        p=self.proof();p[-1]=copy.deepcopy(p[0])
        with self.assertRaises(ValueError):q.accept(p)
    def test_numeric_guards(self):
        for key in ('months','artifacts','slides_structurally_checked','financial_charts_verified','database_writes','report_registrations'):
            p=self.proof();p[0][key]+=1
            with self.subTest(key=key),self.assertRaises(ValueError):q.accept(p)
    def test_boolean_count(self):
        p=self.proof();p[0]['database_writes']=False
        with self.assertRaises(ValueError):q.accept(p)
    def test_boundaries(self):
        for key in ('production_connection_used','approvals_changed'):
            p=self.proof();p[0][key]=True
            with self.subTest(key=key),self.assertRaises(ValueError):q.accept(p)
    def test_no_overclaim(self):
        for key in ('native_visual_review','real_viewer_sign_in','fourteen_day_observation'):
            p=self.proof();p[0][key]='passed'
            with self.subTest(key=key),self.assertRaises(ValueError):q.accept(p)
    def test_receipt_scope(self):
        p=self.proof();p[0]['receipt_blob']='internal-reports/file.pdf'
        with self.assertRaises(ValueError):q.accept(p)
    def test_probe_preserves_full_input_guards(self):
        s=q.probe_source();compile(s,'private-onboard-probe','exec')
        self.assertIn('len(rows)!=21',s);self.assertIn('len(rows)!=2',s)
        self.assertIn('nav_coverage_audit(p)',s)
        self.assertIn("'months':2,'artifacts':4",s)
        self.assertNotIn('candidate-stage34-historical-v1/',s)

if __name__=='__main__':unittest.main()
