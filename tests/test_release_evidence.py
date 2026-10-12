import copy
import tempfile
import unittest
from pathlib import Path
from ibkr_etl.release_evidence import REQUIRED_GATES, validate_release_evidence
from ibkr_etl.source_archive import build_archive, sha256


class ReleaseEvidenceTests(unittest.TestCase):
    def manifest(self):
        digest = "sha256:" + "a" * 64
        return {"image_digest": digest, "source_commit": "b" * 40, "source_dirty": False,
            "gates": {name: {"status": "pass", "image_digest": digest, "evidence_sha256": "c" * 64} for name in REQUIRED_GATES},
            "sbom_sha256": "d" * 64, "template_manifest_sha256": "e" * 64,
            "build_inputs_manifest_sha256": "9" * 64,
            "migration_manifest_sha256": "f" * 64, "verified_rollback_image": "sha256:" + "1" * 64}

    def test_complete_evidence_validates_without_deploying(self):
        self.assertIsNone(validate_release_evidence(self.manifest()))

    def test_no_missing_gate_or_wrong_image_may_pass(self):
        for name in REQUIRED_GATES:
            for fault in ("missing", "failed", "wrong_image"):
                manifest = copy.deepcopy(self.manifest())
                if fault == "missing":
                    del manifest["gates"][name]
                elif fault == "failed":
                    manifest["gates"][name]["status"] = "fail"
                else:
                    manifest["gates"][name]["image_digest"] = "sha256:" + "2" * 64
                with self.assertRaises(ValueError):
                    validate_release_evidence(manifest)

    def test_dirty_or_mutable_releases_and_missing_artifacts_fail(self):
        for field, value in (("source_dirty", True), ("image_digest", "latest"),
                             ("sbom_sha256", None), ("template_manifest_sha256", None),
                             ("migration_manifest_sha256", None), ("verified_rollback_image", None),
                             ("build_inputs_manifest_sha256", None)):
            manifest = self.manifest()
            manifest[field] = value
            with self.assertRaises(ValueError):
                validate_release_evidence(manifest)

    def archive_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "src/app.py").write_text("value = 1\n", encoding="utf-8")
            archive, inventory = build_archive(root, ["src/app.py"])
        manifest = self.manifest()
        del manifest["source_commit"], manifest["source_dirty"]
        manifest["source"] = {"kind": "archive", "archive_sha256": sha256(archive),
                              "tree_sha256": inventory["tree_sha256"]}
        for gate in manifest["gates"].values():
            gate.update({"source_archive_sha256": sha256(archive),
                         "source_tree_sha256": inventory["tree_sha256"]})
            for field in ("build_inputs_manifest_sha256", "template_manifest_sha256",
                          "migration_manifest_sha256"):
                gate[field] = manifest[field]
        return manifest, archive

    def test_archive_mode_validates_exact_bytes_without_git_or_authority(self):
        manifest, archive = self.archive_fixture()
        before = copy.deepcopy(manifest)
        self.assertIsNone(validate_release_evidence(manifest, source_archive_bytes=archive))
        self.assertEqual(before, manifest)
        self.assertNotIn("deployment_authorized", manifest)

    def test_archive_mode_requires_bytes_not_merely_declared_hashes(self):
        manifest, _ = self.archive_fixture()
        for missing in (None, "source.zip", b"not an archive"):
            with self.subTest(value=type(missing)), self.assertRaises(ValueError):
                validate_release_evidence(manifest, source_archive_bytes=missing)

    def test_archive_and_tree_tampering_fail(self):
        original, archive = self.archive_fixture()
        for field in ("archive_sha256", "tree_sha256"):
            manifest = copy.deepcopy(original)
            manifest["source"][field] = "0" * 64
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_release_evidence(manifest, source_archive_bytes=archive)
        with self.assertRaises(ValueError):
            validate_release_evidence(original, source_archive_bytes=archive + b"changed")

    def test_non_zip_with_matching_outer_hash_fails_safely(self):
        manifest, _ = self.archive_fixture()
        data = b"not a zip"
        manifest["source"]["archive_sha256"] = sha256(data)
        with self.assertRaisesRegex(ValueError, "verification failed"):
            validate_release_evidence(manifest, source_archive_bytes=data)

    def test_archive_identity_and_input_binding_required_on_every_gate(self):
        original, archive = self.archive_fixture()
        for name in REQUIRED_GATES:
            for field in ("source_archive_sha256", "source_tree_sha256",
                          "build_inputs_manifest_sha256", "template_manifest_sha256",
                          "migration_manifest_sha256"):
                for value in (None, "0" * 64):
                    manifest = copy.deepcopy(original)
                    manifest["gates"][name][field] = value
                    with self.subTest(gate=name, field=field, value=value), self.assertRaises(ValueError):
                        validate_release_evidence(manifest, source_archive_bytes=archive)

    def test_archive_mode_does_not_relax_any_existing_gate(self):
        original, archive = self.archive_fixture()
        for name in REQUIRED_GATES:
            for field, value in (("status", "fail"), ("image_digest", "latest"),
                                 ("evidence_sha256", None)):
                manifest = copy.deepcopy(original)
                manifest["gates"][name][field] = value
                manifest["approved"] = True  # Caller assertions are never permission.
                with self.subTest(gate=name, field=field), self.assertRaises(ValueError):
                    validate_release_evidence(manifest, source_archive_bytes=archive)

    def test_archive_mode_requires_all_artifact_hashes_and_distinct_rollback(self):
        original, archive = self.archive_fixture()
        for field, value in (("sbom_sha256", None), ("template_manifest_sha256", None),
                             ("migration_manifest_sha256", None), ("build_inputs_manifest_sha256", None),
                             ("verified_rollback_image", original["image_digest"])):
            manifest = copy.deepcopy(original)
            manifest[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_release_evidence(manifest, source_archive_bytes=archive)

    def test_ambiguous_or_unrecognized_provenance_fails(self):
        original, archive = self.archive_fixture()
        for field, value in (("source_commit", "b" * 40), ("source_dirty", False)):
            manifest = copy.deepcopy(original)
            manifest[field] = value
            with self.assertRaises(ValueError):
                validate_release_evidence(manifest, source_archive_bytes=archive)
        for source in (None, [], {}, {**original["source"], "kind": "git"},
                       {**original["source"], "approved": True},
                       {**original["source"], "archive_sha256": "latest"}):
            manifest = copy.deepcopy(original)
            manifest["source"] = source
            with self.assertRaises(ValueError):
                validate_release_evidence(manifest, source_archive_bytes=archive)

    def test_archive_bytes_cannot_silently_use_git_mode(self):
        _, archive = self.archive_fixture()
        with self.assertRaises(ValueError):
            validate_release_evidence(self.manifest(), source_archive_bytes=archive)

    def test_malformed_release_and_gates_fail_as_validation_errors(self):
        for manifest in (None, [], "pass"):
            with self.assertRaises(ValueError):
                validate_release_evidence(manifest)
        for gates in (None, [], {name: None for name in REQUIRED_GATES}):
            manifest = self.manifest()
            manifest["gates"] = gates
            with self.assertRaises(ValueError):
                validate_release_evidence(manifest)


if __name__ == "__main__":
    unittest.main()
