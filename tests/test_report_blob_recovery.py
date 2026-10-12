import contextlib
import importlib.util
import io
import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("blob_recovery_probe", Path(__file__).resolve().parents[1] / "scripts/verify_report_blob_recovery.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class Blob:
    def __init__(self, service, container, name, version=None):
        self.service, self.container, self.name, self.version = service, container, name, version

    def data(self):
        versions = self.service.values[(self.container, self.name)]
        return versions[int(self.version) - 1] if self.version else versions[-1]

    def get_blob_properties(self):
        versions = self.service.values[(self.container, self.name)]
        return SimpleNamespace(version_id=str(len(versions)), size=len(self.data()), etag=str(len(versions)))

    def download_blob(self, **kwargs):
        if "etag" in kwargs:
            assert kwargs["etag"] == self.get_blob_properties().etag
        return SimpleNamespace(readall=lambda: self.data())

    def upload_blob(self, content, overwrite=False, **kwargs):
        assert self.container == "input-snapshots", "Production writes prohibited"
        assert "/recovery-drills/" in self.name
        key = self.container, self.name
        if not overwrite:
            assert key not in self.service.values
            self.service.values[key] = []
        else:
            assert kwargs["etag"] == str(len(self.service.values[key]))
        self.service.values[key].append(content)
        self.service.writes += 1
        version = str(len(self.service.values[key]))
        return {"etag": version, "version_id": version}


class Service:
    def __init__(self):
        self.values = {("internal-reports", "example.pdf"): [b"private report fixture"]}
        self.writes = 0
        self.public = None

    def get_container_client(self, name):
        return SimpleNamespace(get_container_properties=lambda: {"public_access": self.public},
                               get_blob_client=lambda path: Blob(self, name, path))

    def get_blob_client(self, container, name, version_id=None):
        return Blob(self, container, name, version_id)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.service = Service()
        self.row = dict(kind="released_report", container_name="internal-reports", blob_name="example.pdf",
                        sha256=probe.digest(b"private report fixture"))

    def run_probe(self, **environment):
        env = {"DATABASE_URL": "fixture", "PORTFOLIO_TENANT_ID": probe.TENANT,
               "RECOVERY_DRILL_ID": "11111111-1111-1111-1111-111111111111", "AZURE_CLIENT_ID": "fixture", **environment}
        cursor = SimpleNamespace(fetchone=lambda: {"transaction_read_only": "on"}, fetchall=lambda: [self.row])
        db = SimpleNamespace(execute=lambda *args: cursor)
        output = io.StringIO()
        with patch.dict(os.environ, env), patch.object(probe.psycopg, "connect", return_value=contextlib.nullcontext(db)), \
                patch.object(probe, "ManagedIdentityCredential"), patch.object(probe, "BlobServiceClient", return_value=self.service), \
                contextlib.redirect_stdout(output):
            probe.main()
        return [json.loads(line.removeprefix("BLOB_RECOVERY ")) for line in output.getvalue().splitlines()]

    def test_three_versions_restore_only_drill_copy(self):
        rows = self.run_probe()
        self.assertTrue(rows[-1]["passed"])
        self.assertEqual(rows[-1]["verified_assets"], 1)
        self.assertEqual(self.service.writes, 3)
        self.assertEqual(self.service.values[("internal-reports", "example.pdf")], [b"private report fixture"])
        self.assertNotIn("private report fixture", json.dumps(rows))
        self.assertTrue(rows[-1]["cleanup_pending"])

    def test_wrong_tenant_rejected_before_writes(self):
        with self.assertRaises(ValueError):
            self.run_probe(PORTFOLIO_TENANT_ID="other")
        self.assertEqual(self.service.writes, 0)

    def test_wrong_registry_hash_rejected_before_writes(self):
        self.row["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            self.run_probe()
        self.assertEqual(self.service.writes, 0)

    def test_unexpected_source_container_rejected(self):
        self.row["container_name"] = "unrelated"
        with self.assertRaises(ValueError):
            self.run_probe()
        self.assertEqual(self.service.writes, 0)

    def test_public_container_rejected(self):
        self.service.public = "blob"
        with self.assertRaises(ValueError):
            self.run_probe()
        self.assertEqual(self.service.writes, 0)

    def test_invalid_drill_id_rejected(self):
        with self.assertRaises(ValueError):
            self.run_probe(RECOVERY_DRILL_ID="../production")
        self.assertEqual(self.service.writes, 0)

    def test_existing_drill_destination_not_overwritten(self):
        self.run_probe()
        with self.assertRaises(AssertionError):
            self.run_probe()
        self.assertEqual(self.service.writes, 3)


if __name__ == "__main__":
    unittest.main()
