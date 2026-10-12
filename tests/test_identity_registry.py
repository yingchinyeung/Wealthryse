from __future__ import annotations

import unittest
import base64
import json
from pathlib import Path

from ibkr_etl.identity_registry import validate_identity_manifest


ROOT = Path(__file__).resolve().parents[1]


class IdentityRegistryTests(unittest.TestCase):
    def _manifest(self):
        return {
            "mode": "replace",
            "users": [
                {
                    "object_id": "11111111-1111-4111-8111-111111111111",
                    "email": "owner@example.com",
                    "display_name": "Owner",
                    "roles": ["owner", "platform_admin"],
                    "entitlements": [
                        {"subject_id": "u16088571", "access_level": "admin"}
                    ],
                },
                {
                    "object_id": "22222222-2222-4222-8222-222222222222",
                    "email": "viewer@example.com",
                    "roles": ["viewer"],
                    "entitlements": [
                        {"subject_id": "U16088571", "access_level": "analyze"}
                    ],
                },
            ],
        }

    def test_manifest_is_normalized_and_integrity_bound(self):
        manifest = validate_identity_manifest(self._manifest())
        self.assertEqual(manifest.mode, "replace")
        self.assertEqual(len(manifest.users), 2)
        self.assertEqual(manifest.role_count, 3)
        self.assertEqual(manifest.entitlement_count, 2)
        self.assertRegex(manifest.sha256, r"^[0-9a-f]{64}$")
        self.assertEqual(manifest.users[0].entitlements[0][0], "U16088571")

    def test_derived_subject_slug_preserves_its_governed_identifier(self):
        value = self._manifest()
        value["users"][1]["entitlements"].append(
            {"subject_id": "hong-kong-china", "access_level": "analyze"}
        )
        manifest = validate_identity_manifest(value)
        self.assertEqual(
            manifest.users[1].entitlements,
            (("U16088571", "analyze"), ("hong-kong-china", "analyze")),
        )

    def test_subject_entitlement_duplicates_are_case_insensitive(self):
        value = self._manifest()
        value["users"][1]["entitlements"].append(
            {"subject_id": "u16088571", "access_level": "read"}
        )
        with self.assertRaisesRegex(RuntimeError, "invalid subject entitlement"):
            validate_identity_manifest(value)

    def test_exactly_one_owner_is_required(self):
        value = self._manifest()
        value["users"][0]["roles"] = ["platform_admin"]
        with self.assertRaisesRegex(RuntimeError, "exactly one owner"):
            validate_identity_manifest(value)

    def test_duplicate_identity_and_subject_entitlement_fail_closed(self):
        value = self._manifest()
        value["users"][1]["email"] = "owner@example.com"
        with self.assertRaisesRegex(RuntimeError, "duplicate identity"):
            validate_identity_manifest(value)
        value = self._manifest()
        value["users"][0]["entitlements"].append(
            {"subject_id": "U16088571", "access_level": "read"}
        )
        with self.assertRaisesRegex(RuntimeError, "invalid subject entitlement"):
            validate_identity_manifest(value)

    def test_migration_does_not_store_manifest_or_credentials(self):
        migration = (
            ROOT
            / "migrations"
            / "postgres"
            / "044_identity_registry_and_worker_controls.sql"
        ).read_text(encoding="utf-8").lower()
        self.assertIn("manifest_sha256", migration)
        self.assertNotIn("manifest_json", migration)
        self.assertNotIn("token_value", migration)
        self.assertNotIn("client_secret", migration)

    def test_provisioning_script_supports_shell_safe_base64_transport(self):
        script = (ROOT / "scripts" / "sync_identity_registry.py").read_text(
            encoding="utf-8"
        )
        encoded = base64.b64encode(json.dumps(self._manifest()).encode("utf-8")).decode(
            "ascii"
        )
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
        self.assertEqual(json.loads(decoded)["mode"], "replace")
        self.assertIn("IDENTITY_REGISTRY_B64", script)


if __name__ == "__main__":
    unittest.main()
