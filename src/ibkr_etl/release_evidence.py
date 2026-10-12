"""Fail-closed evidence validation; does not deploy or grant release approval."""
import re
import zipfile
from typing import Any, Mapping

from .source_archive import verify_archive


REQUIRED_GATES = frozenset({"unit_integration", "runtime_smoke", "secret_scan",
    "python_scan", "node_scan", "container_scan", "staging_acceptance",
    "build_input_integrity", "production_rights_review"})


def _source_identity(manifest: Mapping[str, Any], archive: bytes | None) -> dict:
    """Verify frozen archive bytes, or retain the existing clean-Git policy.

    This validates identity, not who reviewed it. A trusted release controller
    must verify evidence artifacts and protected approval independently.
    """
    if "source" not in manifest:
        if archive is not None:
            raise ValueError("Archive bytes require explicit archive provenance")
        if (not re.fullmatch(r"[0-9a-f]{40,64}", str(manifest.get("source_commit", "")))
                or manifest.get("source_dirty") is not False):
            raise ValueError("A Git release requires a clean, committed source revision")
        return {}
    source = manifest["source"]
    if (not isinstance(source, Mapping)
            or set(source) != {"kind", "archive_sha256", "tree_sha256"}
            or source.get("kind") != "archive"
            or "source_commit" in manifest or "source_dirty" in manifest):
        raise ValueError("Explicit, unambiguous archive provenance is required")
    for field in ("archive_sha256", "tree_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(source[field])):
            raise ValueError("Archive provenance requires exact SHA-256 identities")
    if not isinstance(archive, bytes):
        raise ValueError("Frozen source archive bytes are required")
    try:
        inventory = verify_archive(archive, source["archive_sha256"])
    except (ValueError, zipfile.BadZipFile, KeyError, TypeError, AttributeError, UnicodeError):
        raise ValueError("Source archive verification failed") from None
    if inventory["tree_sha256"] != source["tree_sha256"]:
        raise ValueError("Source tree does not match release provenance")
    return {"source_archive_sha256": source["archive_sha256"],
            "source_tree_sha256": source["tree_sha256"]}


def validate_release_evidence(manifest: Mapping[str, Any], *,
                              source_archive_bytes: bytes | None = None) -> None:
    """Check evidence structure/identity only; never confer deployment authority."""
    if not isinstance(manifest, Mapping):
        raise ValueError("Release evidence must be a mapping")
    digest = str(manifest.get("image_digest", ""))
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("Release image must be an immutable digest")
    source_binding = _source_identity(manifest, source_archive_bytes)
    for name in ("sbom_sha256", "template_manifest_sha256", "migration_manifest_sha256",
                 "build_inputs_manifest_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(manifest.get(name, ""))):
            raise ValueError(f"Missing release artifact: {name}")
    if source_binding:
        # An archive must not borrow passing results from another source or
        # build-input/template/migration set, even when the image label matches.
        source_binding.update({name: manifest[name] for name in (
            "build_inputs_manifest_sha256", "template_manifest_sha256", "migration_manifest_sha256")})
    gates = manifest.get("gates", {})
    if not isinstance(gates, Mapping):
        raise ValueError("Release gates must be a mapping")
    for name in sorted(REQUIRED_GATES):
        gate = gates.get(name, {})
        if not isinstance(gate, Mapping) or gate.get("status") != "pass" or gate.get("image_digest") != digest:
            raise ValueError(f"Release gate is missing, failed or for a different image: {name}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(gate.get("evidence_sha256", ""))):
            raise ValueError(f"Missing hashed evidence: {name}")
        if any(gate.get(field) != value for field, value in source_binding.items()):
            raise ValueError(f"Release gate has mismatched source or build inputs: {name}")
    rollback = str(manifest.get("verified_rollback_image", ""))
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", rollback) or rollback == digest:
        raise ValueError("A distinct verified rollback image is required")
    # An external protected-environment approval remains mandatory. Never
    # interpret a caller-supplied approved=True as permission to deploy.
