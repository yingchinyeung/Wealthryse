"""The onboarding tables rollout: what it sends to the bootstrap job, without Azure or a database."""
import base64
import gzip
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import onboarding_tables_payload as payload  # noqa: E402
import rollout_onboarding_tables as rollout  # noqa: E402


class OnboardingTablesRolloutTest(unittest.TestCase):
    def test_bundle_carries_only_064_and_065_and_every_checksum(self):
        bundle = rollout.bundle()
        self.assertEqual(bundle["targets"], ["064_broker_connections_and_onboarding_cases.sql", "065_onboarding_report_style.sql"])
        self.assertEqual(set(bundle["target_sql"]), set(bundle["targets"]))
        self.assertIn("063_live_ibkr_data.sql", bundle["other_rollouts"])
        self.assertEqual((bundle["web_role"], bundle["approval_role"]), ("ibkr_stg_web", "ibkr_stg_approval"))
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{64}", d) for d in bundle["expected"].values()))

    def test_command_runs_the_payload_with_the_bundle(self):
        bundle = rollout.bundle()
        args = rollout.command(bundle)
        packed = re.search(r"b64decode\('([^']+)'\)", args[1]).group(1)
        source = gzip.decompress(base64.b64decode(packed)).decode()
        self.assertNotIn("BUNDLE = None", source)
        self.assertTrue(source.rstrip().endswith("raise SystemExit(main())"))
        namespace = {}
        exec(source.replace("raise SystemExit(main())", ""), namespace)
        self.assertEqual(namespace["decode"](namespace["BUNDLE"]), bundle)

    def test_the_job_gets_only_the_admin_url(self):
        container = {"name": "postgres-bootstrap", "image": "x", "env": [{"name": "OTHER", "value": "1"}], "probes": []}
        value = rollout.template(container, ["-c", "pass"])["containers"][0]
        self.assertEqual(value["env"], [{"name": "DATABASE_ADMIN_URL", "secretRef": "database-admin-url"}])
        self.assertNotIn("probes", value)

    def test_the_website_writes_through_the_approval_role_without_deleting(self):
        self.assertIn("broker_onboarding_cases", payload.WRITABLE)
        self.assertEqual(payload.APPEND_ONLY, ("broker_onboarding_events",))
        bootstrap = (ROOT / "scripts" / "bootstrap_postgres.py").read_text()
        self.assertIn("GRANT SELECT, INSERT ON TABLE broker_onboarding_events TO {}", bootstrap)
        for table in payload.WRITABLE:
            self.assertIn(table, bootstrap)


if __name__ == "__main__":
    unittest.main()
