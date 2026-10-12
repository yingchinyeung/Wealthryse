import copy,json,unittest
from pathlib import Path
from deploy_upgrade_inception_20261009 import patch,accept
from deploy_ms_presentation_worker_release import settings_invariant

class ScopeTests(unittest.TestCase):
    def baseline(self):
        p=Path(__file__).resolve().parents[1]/'.work/platform-upgrade-20261009-stage7/risk-deployment-v2/plan.json'
        return json.loads(p.read_bytes())['before']
    def test_only_image_changes(self):
        old=self.baseline();saved=copy.deepcopy(old);body=patch(old,'registry/repo@sha256:'+'a'*64)
        after=copy.deepcopy(old);after['properties']['template']=body['properties']['template']
        self.assertEqual(old,saved)
        self.assertEqual(settings_invariant(old),settings_invariant(after))
        self.assertEqual(after['properties']['template']['containers'][0]['image'],'registry/repo@sha256:'+'a'*64)
    def test_exact_original_image_recovery(self):
        old=self.baseline();original=old['properties']['template']['containers'][0]['image']
        changed=copy.deepcopy(old);changed['properties']['template']=patch(old,'candidate')['properties']['template']
        recovered=copy.deepcopy(changed);recovered['properties']['template']=patch(changed,original)['properties']['template']
        self.assertEqual(settings_invariant(recovered),settings_invariant(old))
        self.assertEqual(recovered['properties']['template']['containers'][0]['image'],original)
    def test_extra_container_rejected(self):
        old=self.baseline();old['properties']['template']['containers']*=2
        with self.assertRaises(ValueError):patch(old,'candidate')
    def proof(self):
        return {'passed':True,'image':'candidate','job_settings_preserved':True,'actual_inception_boundaries_checked':True,'history_coverage':{'passed':True,'live_database_writes':0,'expected':1798,'unique_checked':1798}}
    def test_complete_history_accepted(self):
        accept(self.proof(),'candidate')
    def test_partial_history_rejected(self):
        proof=self.proof();proof['history_coverage']['unique_checked']=100
        with self.assertRaises(ValueError):accept(proof,'candidate')
    def test_old_rehearsal_without_coverage_rejected(self):
        proof=self.proof();proof.pop('history_coverage')
        with self.assertRaises(ValueError):accept(proof,'candidate')
    def test_failed_normal_worker_rejected(self):
        proof=self.proof();proof['passed']=False
        with self.assertRaises(ValueError):accept(proof,'candidate')
    def test_live_write_or_wrong_image_rejected(self):
        proof=self.proof();proof['history_coverage']['live_database_writes']=1
        with self.assertRaises(ValueError):accept(proof,'candidate')
        with self.assertRaises(ValueError):accept(self.proof(),'other')

if __name__=='__main__':unittest.main()
