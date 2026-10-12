from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class InstitutionalControlPlaneAcceptanceTests(unittest.TestCase):
    def test_acceptance_script_is_non_sensitive_and_checksum_bound(self):
        script = (ROOT / "scripts" / "verify_institutional_control_plane.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("schema_migrations", script)
        self.assertIn("expected_digest", script)
        self.assertIn("045_institutional_shadow_reconciliation.sql", script)
        self.assertIn("database_authorization_ready", script)
        self.assertIn("shadow_reconciliation", script)
        self.assertIn('"dead_letters"', script)
        self.assertNotIn("anthony.ying", script.lower())
        self.assertNotIn("entra_object_id", script.lower())
        self.assertNotIn("email", script.lower())
        self.assertNotIn("token", script.lower())


if __name__ == "__main__":
    unittest.main()
