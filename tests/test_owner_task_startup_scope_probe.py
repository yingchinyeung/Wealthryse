"""Scope probe guards; synthetic metadata only, never a live database test."""
import copy
import gzip
import base64
import pathlib
import sys
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import owner_task_startup_scope_probe as probe
from test_owner_task_postgres_deployment import job


class ScopeProbeTests(unittest.TestCase):
    def test_absence_exact_result(self):
        value = dict(status='passed',trial_database_absent=True,trial_role_absent=True,
            production_database_touched=False,ibkr_source_writes=False,ddl_executed=False,
            existing_service_grants_changed=False,model_calls=0)
        self.assertEqual(probe.validate_records([value]),value)

    def test_present_role_denied(self):
        with self.assertRaises(ValueError):
            probe.validate_records([dict(status='passed',trial_role_absent=False)])

    def test_missing_and_duplicate_results_denied(self):
        for value in ([],[{},{}]):
            with self.subTest(value=value),self.assertRaises(ValueError): probe.validate_records(value)

    def test_scoped_probe_keeps_template_and_secret_refs(self):
        original = job()
        with patch.object(probe.deploy,'bundle',return_value=(['-c','original'],dict(files={},payload_sha256='fixed'))):
            wanted,source = probe.frozen_template(original)
        self.assertEqual(wanted['containers'][0]['env'],[
            dict(name='DATABASE_ADMIN_URL',secretRef='database-admin-url'),
            dict(name='OWNER_TASK_TRIAL_STAGE',value='scope-probe')])
        self.assertEqual(original,job())
        encoded = wanted['containers'][0]['args'][1].split("base64.b64decode('",1)[1].split("'",1)[0]
        code = gzip.decompress(base64.b64decode(encoded)).decode()
        compile(code,'synthetic-probe','exec')
        self.assertIn("scope={'__name__':'owner_task_scope_probe'}",code)
        self.assertIn("database,role=scope['metadata'](connection)",code)
        self.assertNotIn("scope['main'](",code)
        self.assertNotIn("scope['setup'](",code)
        self.assertNotIn("scope['cleanup'](",code)
        self.assertEqual(len(source['probe_sha256']),64)


if __name__ == '__main__':
    unittest.main()
