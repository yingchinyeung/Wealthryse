import base64
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import inspect_agent_inbox_release as inspection
import deploy_specialist_report_worker as deployment


class InboxReleaseTests(unittest.TestCase):
    def test_fingerprint_payload_has_no_database_or_mail_operations(self):
        args,digest=inspection.bundle()
        source=base64.b64decode(args[1].split("b64decode('")[1].split("')")[0]).decode()
        compile(source,'inspect','exec')
        self.assertEqual(len(digest),64)
        for forbidden in ('psycopg','os.environ','requests','send_mail','write_text'):
            self.assertNotIn(forbidden,source)
        self.assertIn('ast.parse',source)

    def test_execution_override_preserves_installed_job_and_secret_scope(self):
        job=deployment.definition();before=copy.deepcopy(job)
        image='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:'+'a'*64
        wanted=inspection.template(job,dict(image=image))
        self.assertEqual(job,before)
        self.assertEqual(wanted['containers'][0]['image'],image)
        self.assertEqual(wanted['containers'][0]['env'],job['properties']['template']['containers'][0]['env'])

    def test_receipt_rejects_extra_paths_or_mutations(self):
        valid=dict(status='passed',database_connections_opened=0,source_rows_changed=0,messages_sent=0,
            financial_values_logged=False,files=[dict(path=p,present=False,sha256=None) for p in inspection.PATHS],routes=[])
        inspection.validate_record(valid)
        for change in ({'source_rows_changed':1},{'messages_sent':True},{'files':[]},{'routes':[dict(path='/administration',static_database_owner_guard=True)]}):
            with self.assertRaises(ValueError):inspection.validate_record({**valid,**change})


if __name__=='__main__':unittest.main()
