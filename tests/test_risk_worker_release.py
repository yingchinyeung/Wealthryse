import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from start_risk_candidate import candidate_template
from ibkr_etl.source_archive import allowed_path
from pin_risk_worker_release import validate_execution, IMAGE
import json


class RiskWorkerReleaseTests(unittest.TestCase):
    def test_renderer_free_source_recipe(self):
        docker=Path('Dockerfile.risk').read_text()
        self.assertIn('python:3.12-slim@sha256:',docker)
        self.assertIn('--require-hashes -r requirements.lock',docker)
        self.assertIn('USER 10001:10001',docker)
        self.assertIn('RUN python scripts/check_risk_runtime.py',docker)
        self.assertNotIn('COPY . ',docker)
        for value in ('node_modules','COPY templates','COPY private-inputs'):
            self.assertNotIn(value,docker)
        self.assertTrue(allowed_path('Dockerfile.risk'))

    def test_risk_recipe_pins_available_stable_security_updates(self):
        docker=Path('Dockerfile.risk').read_text()
        for package in ('libssl3t64', 'openssl', 'openssl-provider-legacy'):
            self.assertIn(package + '=3.5.7-1~deb13u3', docker)
        self.assertIn('libpcre2-8-0=10.46-1~deb13u3', docker)
        self.assertIn('--only-upgrade', docker)
        self.assertIn('dpkg-query --show', docker)
        self.assertNotIn('--allow-unauthenticated', docker)
        self.assertNotIn('--allow-downgrades', docker)

    def test_candidate_preserves_environment_and_resources(self):
        original={'containers':[{'name':'risk','image':'old','env':[{'name':'DATABASE_URL','secretRef':'db'}],
                    'command':['python'],'args':['old.py'],'resources':{'cpu':1,'memory':'2Gi'}}]}
        baseline=copy.deepcopy(original)
        result=candidate_template(original,'sha256:'+'a'*64,'ibkr-risk-worker')
        self.assertEqual(original,baseline)
        self.assertEqual(result['containers'][0]['env'],original['containers'][0]['env'])
        self.assertEqual(result['containers'][0]['resources'],original['containers'][0]['resources'])
        self.assertIn('/ibkr-risk-worker@sha256:',result['containers'][0]['image'])

    def test_unreviewed_image_repository_rejected(self):
        with self.assertRaises(ValueError):
            candidate_template({},'sha256:'+'a'*64,'other-registry')

    def test_mutable_image_reference_rejected(self):
        with self.assertRaises(ValueError):
            candidate_template({},'latest','ibkr-risk-worker')

    def test_promotion_binds_success_to_exact_image_command_and_summary(self):
        state={'properties':{'status':'Succeeded','template':{'containers':[{
            'image':IMAGE,'command':['python'],'args':['scripts/build_risk_snapshots.py']}]}}}
        summary={'mode':'latest','skipped_existing':4,'snapshots':0,'status':'success','subjects':0}
        logs=[{'Log_s':json.dumps(summary)}]
        self.assertEqual(validate_execution(state,logs),summary)
        for field,value in [('image','unverified'),('args',['other.py']),('command',['sh'])]:
            changed=copy.deepcopy(state)
            changed['properties']['template']['containers'][0][field]=value
            with self.assertRaises(RuntimeError):
                validate_execution(changed,logs)
        with self.assertRaises(RuntimeError):
            validate_execution(state,[])
        with self.assertRaises(RuntimeError):
            validate_execution(state,[{'Log_s':json.dumps(dict(summary,snapshots=1))}])


if __name__=='__main__':
    unittest.main()
