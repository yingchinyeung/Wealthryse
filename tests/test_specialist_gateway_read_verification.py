import base64
import copy
import gzip
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import run_specialist_gateway_read_verification as trial
import deploy_specialist_report_worker as deployment


class ReadVerificationTests(unittest.TestCase):
    def owner(self):
        return dict(status='passed',subject_id='U16088571',tool_name='get_performance_series',account_read_status=200,
            real_entra_signature_verified=True,registered_client_verified=True,invalid_signature_status=401,
            financial_values_logged=False,token_retained=False,observations=12,response_sha256='a'*64,user_sha256='b'*64)

    def test_read_only_payload_and_original_worker_unchanged(self):
        job=deployment.definition();before=copy.deepcopy(job)
        template=trial.template(job,self.owner())
        self.assertEqual(job,before)
        trial.verify_container(template['containers'],template['containers'][0])
        args,digest=trial.bundle(self.owner())
        source=gzip.decompress(base64.b64decode(args[1].split("b64decode('")[1].split("')")[0])).decode()
        compile(source,'trial','exec')
        self.assertIn("EXPECTED_RESPONSE_SHA256='"+'a'*64+"'",source)
        self.assertNotIn('dispatch_daily_email',source)
        self.assertIn('source_table_access=False,database_writes=0,messages_sent=0',source)
        self.assertLess(len(args[1]),30000)
        self.assertEqual(len(digest),64)

    def test_scope_and_signin_evidence_fail_closed(self):
        for changes in ({'subject_id':'U16088606'},{'real_entra_signature_verified':False},
                        {'response_sha256':'invalid'},{'token_retained':True},{'observations':True}):
            with self.assertRaises(ValueError): trial.validate_owner({**self.owner(),**changes})

    def test_execution_source_credentials_and_capacity_checked(self):
        wanted=trial.template(deployment.definition(),self.owner())['containers'][0]
        for changes in ({'image':'other'},{'args':['other']},{'env':[dict(name='DATABASE_URL',secretRef='admin-url')]},
                        {'resources':dict(cpu=2,memory='1Gi')}):
            actual={**copy.deepcopy(wanted),**changes}
            with self.assertRaises(ValueError): trial.verify_container([actual],wanted)


if __name__=='__main__': unittest.main()
