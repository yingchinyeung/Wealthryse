import tempfile
import unittest
from pathlib import Path

from ibkr_etl.build_inputs import (classify_bundled_licence, fingerprint_file,
                                   fingerprint_tree, require_production_inputs)


class BuildInputTests(unittest.TestCase):
    def test_exact_bytes_and_tree_change_are_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "assets").mkdir()
            path = root / "assets/template.pdf"
            path.write_bytes(b"first")
            before = fingerprint_tree(root, "assets")
            self.assertEqual(before, fingerprint_tree(root, "assets"))
            path.write_bytes(b"other")
            self.assertNotEqual(before["tree_sha256"], fingerprint_tree(root, "assets")["tree_sha256"])

    def test_traversal_absolute_and_missing_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            for relative in ("../outside", "/absolute", "C:/absolute", "missing", "assets\\file"):
                with self.subTest(path=relative), self.assertRaises((ValueError, FileNotFoundError)):
                    fingerprint_file(Path(directory), relative)

    def test_restricted_licence_never_allows_production(self):
        result = classify_bundled_licence("solely for internal evaluation and testing purposes; "
                                          "not for production, commercial, or benchmarking purposes")
        self.assertEqual(result["status"], "production_restricted")
        self.assertFalse(result["production_authorized"])

    def test_unknown_or_missing_licence_never_defaults_to_pass(self):
        for text in ("", "an unknown licence", "MIT"):
            result = classify_bundled_licence(text)
            self.assertEqual(result["status"], "review_required")
            self.assertFalse(result["production_authorized"])

    def test_boolean_approval_cannot_override_rights_review(self):
        for status in ("production_restricted", "approved", "review_required"):
            with self.assertRaises(ValueError):
                require_production_inputs({"schema": "wealthryse-build-input-inventory-v1",
                    "runtime_licence": {"status": status}, "production_build_ready": True,
                    "deployment_authorized": True})


if __name__ == "__main__":
    unittest.main()
