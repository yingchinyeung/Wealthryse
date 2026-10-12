import copy
from datetime import datetime, timezone
import json
import unittest

from ibkr_etl.container_scan import assess_container_scan


class ContainerScanTests(unittest.TestCase):
    def setUp(self):
        self.digest, self.config = "sha256:" + "a" * 64, "sha256:" + "b" * 64
        self.now = datetime(2026, 10, 6, 18, tzinfo=timezone.utc)
        self.report = {"SchemaVersion": 2, "ArtifactType": "container_image",
                       "CreatedAt": self.now.isoformat(), "Trivy": {"Version": "0.75.0"},
                       "Metadata": {"ImageID": self.config, "OS": {"Family": "debian", "Name": "13"}},
                       "Results": []}
        for kind in ("debian", "python-pkg", "node-pkg"):
            self.report["Results"].append({"Target": kind, "Type": kind,
                "Class": "os-pkgs" if kind == "debian" else "lang-pkgs",
                "Packages": [{"Identifier": {"PURL": "pkg:test/" + kind}}]})
        self.bom = {"bomFormat": "CycloneDX", "specVersion": "1.7",
                    "metadata": {"timestamp": self.now.isoformat(), "component": {
                        "properties": [{"name": "aquasecurity:trivy:ImageID", "value": self.config}]}},
                    "components": [{"purl": "pkg:test/" + k} for k in ("debian", "python-pkg", "node-pkg")]}

    def assess(self, **kwargs):
        args = dict(report_bytes=json.dumps(self.report).encode(), sbom_bytes=json.dumps(self.bom).encode(),
                    image_digest=self.digest, config_digest=self.config, scanner_version="0.75.0", now=self.now)
        args.update(kwargs)
        return assess_container_scan(**args)

    def test_complete_clean_scan_is_not_deployment_authority(self):
        result = self.assess()
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["scanned_packages"], 3)
        self.assertEqual(result["unique_package_purls"], 3)
        self.assertFalse(result["deployment_authorized"])

    def test_high_critical_unknown_block_even_without_available_fix(self):
        for severity in ("CRITICAL", "HIGH", "UNKNOWN", "MEDIUM", "LOW"):
            self.report["Results"][0]["Vulnerabilities"] = [{"VulnerabilityID": "CVE-test",
                "PkgName": "package", "InstalledVersion": "1", "Severity": severity}]
            with self.subTest(severity=severity):
                self.assertEqual(self.assess()["status"], "pass" if severity in ("LOW", "MEDIUM") else "blocked")

    def test_missing_scan_coverage_cannot_be_clean(self):
        for kind in ("debian", "python-pkg", "node-pkg"):
            original = self.report["Results"]
            self.report["Results"] = [r for r in original if r["Type"] != kind]
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.assess()
            self.report["Results"] = original

    def test_python_only_build_requires_explicit_trusted_policy(self):
        self.report["Results"] = [r for r in self.report["Results"] if r["Type"] != "node-pkg"]
        with self.assertRaises(ValueError):
            self.assess()
        self.assertEqual(self.assess(required_types=frozenset(("debian", "python-pkg")))["status"], "pass")
        for value in (set(("debian", "python-pkg")), frozenset(), frozenset(("debian",)),
                      frozenset(("debian", "python-pkg", "unknown"))):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.assess(required_types=value)

    def test_malformed_results_rejected(self):
        original = copy.deepcopy(self.report)
        for value in (None, [], {}, [None]):
            self.report = copy.deepcopy(original)
            self.report["Results"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.assess()
        self.report = original
        self.report["Results"].append(copy.deepcopy(self.report["Results"][0]))
        with self.assertRaises(ValueError):
            self.assess()

    def test_package_inventory_required(self):
        for value in (None, [], {}, [{}], [{"Identifier": {"PURL": "not-a-package"}}]):
            self.report["Results"][0]["Packages"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.assess()

    def test_unknown_finding_shape_never_means_no_vulnerability(self):
        for value in (None, {}, [None], [{"Severity": "NEW"}], [{"Severity": "HIGH"}]):
            self.report["Results"][0]["Vulnerabilities"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.assess()

    def test_wrong_config_or_scanner_or_eol_rejected(self):
        for key, value in (("config_digest", self.digest), ("scanner_version", "0.75.1"),
                           ("image_digest", "latest"), ("now", datetime(2026, 10, 6))):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.assess(**{key: value})
        self.report["Metadata"]["OS"]["EOSL"] = True
        with self.assertRaises(ValueError):
            self.assess()

    def test_stale_future_naive_output_rejected(self):
        for stamp in ("2026-10-05T17:59:59Z", "2026-10-06T18:00:01Z", "2026-10-06T18:00:00"):
            self.report["CreatedAt"] = stamp
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                self.assess()

    def test_sbom_config_and_package_coverage_required(self):
        original = copy.deepcopy(self.bom)
        self.bom["metadata"]["component"]["properties"][0]["value"] = self.digest
        with self.assertRaises(ValueError):
            self.assess()
        self.bom = copy.deepcopy(original)
        self.bom["components"].pop()
        with self.assertRaises(ValueError):
            self.assess()
        self.bom = copy.deepcopy(original)
        self.bom["metadata"]["component"]["properties"].append(copy.deepcopy(
            self.bom["metadata"]["component"]["properties"][0]))
        with self.assertRaises(ValueError):
            self.assess()

    def test_invalid_duplicate_and_nonfinite_json_rejected(self):
        for data in (b'{}', b'[]', b'null', b'{"a":1,"a":2}', b'{"a":NaN}', b'\xff'):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.assess(report_bytes=data)

    def test_malformed_metadata_and_unhashable_fields_fail_closed(self):
        for value in (None, [], "metadata"):
            self.bom["metadata"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.assess()
        self.setUp()
        self.report["Results"][0]["Type"] = []
        with self.assertRaises(ValueError):
            self.assess()
        self.setUp()
        self.report["Results"][0]["Vulnerabilities"] = [{"Severity": []}]
        with self.assertRaises(ValueError):
            self.assess()


if __name__ == "__main__":
    unittest.main()
