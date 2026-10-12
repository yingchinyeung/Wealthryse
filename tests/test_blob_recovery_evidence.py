import copy
import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("recovery_archive", SCRIPTS / "archive_blob_recovery_evidence.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.dispatch = {"execution": "ibkrv2stg-bootstrap-1234567", "run_id": "fixture"}
        prefix = f"{module.DB_TENANT}/recovery-drills/fixture/"
        self.result = {"execution": self.dispatch["execution"], "status": "Succeeded", "job_settings_unchanged": True,
            "records": [
                {"run_id": "fixture", "kind": "inventory", "registry_rows": 1},
                {"run_id": "fixture", "kind": "released_report", "index": 0, "artifact_sha256": "a" * 64,
                 "recovered": True, "original_unchanged": True, "source_version": "old", "bytes": 12,
                 "drill_blob": prefix + "000-" + "a" * 64 + ".recovery", "versions": ["1", "2", "3"]},
                {"run_id": "fixture", "kind": "complete", "passed": True, "verified_assets": 1,
                 "registry_rows": 1, "database_read_only": True, "originals_modified": False,
                 "drill_prefix": prefix, "verified_bytes": 12}]}

    def test_complete_version_evidence_accepted(self):
        self.assertTrue(module.validate(self.result, self.dispatch)["passed"])

    def test_failed_running_or_changed_execution_rejected(self):
        for key, value in (("status", "Running"), ("status", "Failed"), ("job_settings_unchanged", False), ("execution", "other")):
            with self.subTest(key=key, value=value):
                result = copy.deepcopy(self.result)
                result[key] = value
                with self.assertRaises(ValueError): module.validate(result, self.dispatch)

    def test_asset_integrity_errors_rejected(self):
        for key, value in (("versions", ["1", "1", "3"]), ("versions", ["1", "2", None]),
                           ("original_unchanged", False), ("source_version", None), ("bytes", 13),
                           ("drill_blob", "production/report"), ("index", 1), ("run_id", "other")):
            with self.subTest(key=key):
                result = copy.deepcopy(self.result)
                result["records"][1][key] = value
                with self.assertRaises(ValueError): module.validate(result, self.dispatch)

    def test_missing_duplicate_and_unknown_evidence_rejected(self):
        for operation in ("missing", "duplicate", "unknown"):
            result = copy.deepcopy(self.result)
            if operation == "missing": result["records"].pop(1)
            elif operation == "duplicate": result["records"].append(copy.deepcopy(result["records"][-1]))
            else: result["records"].append({"run_id": "fixture", "kind": "unexpected"})
            with self.assertRaises(ValueError): module.validate(result, self.dispatch)
