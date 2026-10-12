import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ibkr_etl.release_evidence import REQUIRED_GATES
from ibkr_etl.release_package import release_subject_sha256, verify_release_package
from ibkr_etl.source_archive import build_archive, canonical, sha256


class ReleasePackageTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 2, 1, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "src").mkdir()
            (root / "src/app.py").write_text("value = 1\n", encoding="utf-8")
            self.archive, inventory = build_archive(root, ["src/app.py"])
        self.artifacts = {}
        self.attestations = {}
        self.allowed = {gate: frozenset({"trusted-test-producer"}) for gate in REQUIRED_GATES}
        self.manifest = {"schema": "wealthryse-release-package-v1",
            "image_digest": "sha256:" + "a" * 64,
            "source": {"kind": "archive", "archive_sha256": sha256(self.archive),
                       "tree_sha256": inventory["tree_sha256"]},
            "verified_rollback_image": "sha256:" + "b" * 64, "gates": {}}
        for field in ("sbom_sha256", "template_manifest_sha256", "migration_manifest_sha256",
                      "build_inputs_manifest_sha256"):
            self.manifest[field] = self.add(canonical({"fixture": field}))
        self.targets = canonical({"schema": "wealthryse-release-targets-v1", "targets": [{
            "resource_id": "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/test-rg/providers/Microsoft.App/jobs/test-job",
            "configuration_sha256": "c" * 64, "previous_image_digest": "sha256:" + "b" * 64}]})
        self.manifest["target_manifest_sha256"] = self.add(self.targets)
        subject = release_subject_sha256(self.manifest)
        for name in REQUIRED_GATES:
            output = self.add(canonical({"fixture_gate": name, "result": "test fixture only"}))
            evidence = {"schema": "wealthryse-release-gate-v1", "gate": name, "status": "pass",
                "subject_sha256": subject, "producer_id": "trusted-test-producer",
                "completed_at": (self.now - timedelta(minutes=5)).isoformat(),
                "valid_until": (self.now + timedelta(hours=1)).isoformat(), "output_sha256s": [output]}
            digest = self.add(canonical(evidence))
            self.attestations[digest] = "trusted-test-producer"
            self.manifest["gates"][name] = {"status": "pass", "image_digest": self.manifest["image_digest"],
                "evidence_sha256": digest, "source_archive_sha256": sha256(self.archive),
                "source_tree_sha256": inventory["tree_sha256"],
                **{key: self.manifest[key] for key in ("build_inputs_manifest_sha256",
                    "template_manifest_sha256", "migration_manifest_sha256")}}

    def add(self, content):
        digest = sha256(content)
        self.artifacts[digest] = content
        return digest

    def change_gate(self, change):
        name = "unit_integration"
        old = self.manifest["gates"][name]["evidence_sha256"]
        gate = json.loads(self.artifacts.pop(old))
        change(gate)
        digest = self.add(canonical(gate))
        self.manifest["gates"][name]["evidence_sha256"] = digest
        self.attestations[digest] = self.attestations.pop(old)
        return digest

    def verify(self, **overrides):
        data = canonical(self.manifest)
        kwargs = dict(manifest_bytes=data, expected_manifest_sha256=sha256(data),
            source_archive_bytes=self.archive, artifacts=self.artifacts,
            current_target_manifest_bytes=self.targets, attested_producers=self.attestations,
            allowed_producers=self.allowed, now=self.now)
        kwargs.update(overrides)
        return verify_release_package(**kwargs)

    def test_complete_fixture_verifies_without_approval_or_mutation(self):
        before = copy.deepcopy((self.manifest, self.artifacts, self.attestations))
        result = self.verify()
        self.assertFalse(result["deployment_authorized"])
        self.assertFalse(result["approval_recorded"])
        self.assertEqual(result["verified_gates"], sorted(REQUIRED_GATES))
        self.assertEqual(before, (self.manifest, self.artifacts, self.attestations))

    def test_missing_or_changed_supporting_bytes_fail(self):
        for field in ("sbom_sha256", "template_manifest_sha256", "migration_manifest_sha256",
                      "build_inputs_manifest_sha256", "target_manifest_sha256"):
            for replacement in (None, b"changed"):
                artifacts = dict(self.artifacts)
                digest = self.manifest[field]
                if replacement is None:
                    del artifacts[digest]
                else:
                    artifacts[digest] = replacement
                with self.subTest(field=field), self.assertRaises(ValueError):
                    self.verify(artifacts=artifacts)

    def test_every_gate_requires_actual_bytes_and_attestation(self):
        for gate in REQUIRED_GATES:
            digest = self.manifest["gates"][gate]["evidence_sha256"]
            artifacts = dict(self.artifacts)
            del artifacts[digest]
            with self.subTest(gate=gate), self.assertRaises(ValueError):
                self.verify(artifacts=artifacts)
            attestations = dict(self.attestations)
            del attestations[digest]
            with self.subTest(gate=gate), self.assertRaises(ValueError):
                self.verify(attested_producers=attestations)

    def test_producer_assertion_in_upload_is_not_trust(self):
        with self.assertRaisesRegex(ValueError, "attestation"):
            self.verify(attested_producers={})
        for policy in ({}, {name: frozenset({"different-producer"}) for name in REQUIRED_GATES},
                       {name: "trusted-test-producer" for name in REQUIRED_GATES}):
            with self.assertRaises(ValueError):
                self.verify(allowed_producers=policy)

    def test_attested_identity_must_equal_gate_identity(self):
        self.change_gate(lambda gate: gate.update(producer_id="untrusted-producer"))
        with self.assertRaisesRegex(ValueError, "attestation"):
            self.verify()

    def test_changed_candidate_cannot_reuse_gate_results(self):
        self.manifest["verified_rollback_image"] = "sha256:" + "e" * 64
        with self.assertRaisesRegex(ValueError, "candidate"):
            self.verify()

    def test_changed_targets_fail_before_approval(self):
        targets = json.loads(self.targets)
        targets["targets"][0]["configuration_sha256"] = "e" * 64
        with self.assertRaisesRegex(ValueError, "targets changed"):
            self.verify(current_target_manifest_bytes=canonical(targets))

    def test_actual_gate_status_overrides_manifest_pass_claim(self):
        self.change_gate(lambda gate: gate.update(status="fail"))
        with self.assertRaisesRegex(ValueError, "candidate"):
            self.verify()

    def test_stale_future_naive_and_overlong_evidence_fail(self):
        for updates in ({"valid_until": self.now.isoformat()},
                        {"completed_at": (self.now + timedelta(seconds=1)).isoformat()},
                        {"valid_until": (self.now + timedelta(days=2)).isoformat()},
                        {"completed_at": "2026-10-02T00:00:00"},
                        {"completed_at": None}):
            self.setUp()
            self.change_gate(lambda gate: gate.update(updates))
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                self.verify()

    def test_naive_server_clock_fails(self):
        with self.assertRaises(ValueError):
            self.verify(now=self.now.replace(tzinfo=None))

    def test_gate_outputs_required_and_verified(self):
        for outputs in ([], ["0" * 64], [None], "not-a-list", ["0" * 64] * 2):
            self.setUp()
            self.change_gate(lambda gate: gate.update(output_sha256s=outputs))
            with self.subTest(outputs=outputs), self.assertRaises(ValueError):
                self.verify()

    def test_duplicate_json_keys_and_nonfinite_numbers_fail(self):
        for data in (b'{"schema":"a","schema":"b"}', b'{"value":NaN}', b'[]', b'not JSON'):
            with self.assertRaises(ValueError):
                self.verify(manifest_bytes=data, expected_manifest_sha256=sha256(data))

    def test_manifest_and_archive_hashes_are_not_optional(self):
        with self.assertRaises(ValueError):
            self.verify(expected_manifest_sha256="0" * 64)
        with self.assertRaises(ValueError):
            self.verify(source_archive_bytes=self.archive + b"changed")

    def test_unreferenced_artifacts_rejected(self):
        self.add(b"unexpected")
        with self.assertRaisesRegex(ValueError, "Unreferenced"):
            self.verify()

    def test_invalid_or_duplicate_targets_rejected(self):
        for fault in ("duplicate", "wrong-resource", "missing-config", "empty"):
            self.setUp()
            targets = json.loads(self.targets)
            if fault == "duplicate":
                targets["targets"].append(copy.deepcopy(targets["targets"][0]))
            elif fault == "wrong-resource":
                targets["targets"][0]["resource_id"] = "https://outside.example"
            elif fault == "missing-config":
                del targets["targets"][0]["configuration_sha256"]
            else:
                targets["targets"] = []
            del self.artifacts[self.manifest["target_manifest_sha256"]]
            self.targets = canonical(targets)
            self.manifest["target_manifest_sha256"] = self.add(self.targets)
            with self.subTest(fault=fault), self.assertRaisesRegex(ValueError, "target"):
                self.verify()

    def test_package_size_and_type_limits(self):
        for artifacts in ({}, {"x": "not bytes"}, {"x": b"a" * (32 * 1024 * 1024 + 1)}):
            with self.assertRaises(ValueError):
                self.verify(artifacts=artifacts)


if __name__ == "__main__":
    unittest.main()
