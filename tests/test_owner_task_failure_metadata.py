"""Fixed disposable catalog diagnosis packaging; no live connections."""
import base64
import gzip
import pathlib
import sys
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import inspect_owner_task_trial_failure as diagnostic
from test_owner_task_postgres_deployment import job


class FailureMetadataTests(unittest.TestCase):
    def test_reviewed_wrapper_never_called_as_main(self):
        wanted,record = diagnostic.template(job())
        encoded = wanted['containers'][0]['args'][1].split("base64.b64decode('",1)[1].split("'",1)[0]
        code = gzip.decompress(base64.b64decode(encoded)).decode()
        compile(code,'fixed-metadata-probe','exec')
        self.assertIn("scope={'__name__':'owner_task_failure_metadata'}",code)
        self.assertIn('default_transaction_read_only=on',code)
        self.assertIn("scope['metadata'](raw)",code)
        for name in ('main','setup','cleanup','check_registered'):
            self.assertNotIn("scope['"+name+"'](",code)
        self.assertEqual(len(record['metadata_probe_sha256']),64)

    def test_database_credential_is_only_a_secret_reference(self):
        wanted,_ = diagnostic.template(job())
        self.assertEqual(wanted['containers'][0]['env'][0],
            dict(name='DATABASE_ADMIN_URL',secretRef='database-admin-url'))
        self.assertEqual(wanted['containers'][0]['env'][1],
            dict(name='OWNER_TASK_TRIAL_STAGE',value='metadata-diagnosis'))

    def test_changed_frozen_sources_denied(self):
        original = diagnostic.deploy.bundle('run')
        with patch.object(diagnostic.deploy,'bundle',side_effect=[original,(['-c','changed'],{})]):
            with self.assertRaises(ValueError): diagnostic.template(job())


if __name__ == '__main__':
    unittest.main()
