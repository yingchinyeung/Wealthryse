"""Offline release-package verification, never deployment authorization.

Producer attestations and current target bytes MUST come from a trusted server
adapter, not a browser/uploaded manifest. This module does not authenticate Azure,
run scans, record approval or dispatch a deployment. Hashes alone are not trust.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Mapping

from .release_evidence import REQUIRED_GATES, validate_release_evidence
from .source_archive import canonical, sha256

MAX_JSON = 2 * 1024 * 1024
MAX_ARTIFACT = 32 * 1024 * 1024
MAX_PACKAGE = 64 * 1024 * 1024
ARTIFACT_FIELDS = ("sbom_sha256", "template_manifest_sha256",
                   "migration_manifest_sha256", "build_inputs_manifest_sha256")


def _json(data: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate release JSON key")
            result[key] = value
        return result

    def invalid_constant(_):
        raise ValueError("Non-finite release JSON value")

    if not isinstance(data, bytes) or len(data) > MAX_JSON:
        raise ValueError("Bounded release JSON bytes required")
    try:
        result = json.loads(data.decode("utf-8"), object_pairs_hook=unique,
                            parse_constant=invalid_constant)
    except (UnicodeError, ValueError, RecursionError):
        raise ValueError("Invalid release JSON") from None
    if not isinstance(result, dict):
        raise ValueError("Release JSON must be an object")
    return result


def _timestamp(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            raise ValueError("Timezone required")
        return parsed.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        raise ValueError("Timezone-aware release evidence timestamp required") from None


def release_subject_sha256(manifest: dict) -> str:
    """Non-circular binding: the entire release declaration except gate records."""
    return sha256(canonical({key: value for key, value in manifest.items() if key != "gates"}))


def _targets(data: bytes) -> None:
    document = _json(data)
    if (set(document) != {"schema", "targets"}
            or document["schema"] != "wealthryse-release-targets-v1"
            or not isinstance(document["targets"], list)
            or not 1 <= len(document["targets"]) <= 32):
        raise ValueError("Invalid deployment target inventory")
    seen = set()
    pattern = (r"/subscriptions/[0-9a-f-]{36}/resourceGroups/[A-Za-z0-9_.()-]+"
               r"/providers/Microsoft.App/(?:containerApps|jobs)/[a-z0-9-]+")
    for target in document["targets"]:
        if (not isinstance(target, dict)
                or set(target) != {"resource_id", "configuration_sha256", "previous_image_digest"}
                or not re.fullmatch(pattern, str(target["resource_id"]))
                or not re.fullmatch(r"[0-9a-f]{64}", str(target["configuration_sha256"]))
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(target["previous_image_digest"]))):
            raise ValueError("Invalid deployment target binding")
        resource = target["resource_id"].lower()
        if resource in seen:
            raise ValueError("Duplicate deployment target")
        seen.add(resource)


def verify_release_package(*, manifest_bytes: bytes, expected_manifest_sha256: str,
                           source_archive_bytes: bytes, artifacts: Mapping[str, bytes],
                           current_target_manifest_bytes: bytes,
                           attested_producers: Mapping[str, str],
                           allowed_producers: Mapping[str, frozenset[str]],
                           now: datetime) -> dict:
    """Return non-authorizing verification receipt from independently trusted inputs.

    attested_producers maps an artifact digest to its authenticated producer;
    a producer_id asserted INSIDE an artifact is insufficient. allowed_producers
    is server-owned policy by gate. Both must be supplied by the future trusted
    adapter. now must be server time. Target bytes must be a fresh ARM readback.
    """
    if (not isinstance(manifest_bytes, bytes)
            or not re.fullmatch(r"[0-9a-f]{64}", str(expected_manifest_sha256))
            or sha256(manifest_bytes) != expected_manifest_sha256):
        raise ValueError("Release manifest identity mismatch")
    manifest = _json(manifest_bytes)
    if manifest.get("schema") != "wealthryse-release-package-v1" or "source" not in manifest:
        raise ValueError("Archive-based release package required")
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ValueError("Trusted timezone-aware server time required")
    now = now.astimezone(timezone.utc)
    validate_release_evidence(manifest, source_archive_bytes=source_archive_bytes)
    if (not isinstance(artifacts, Mapping) or not 1 <= len(artifacts) <= 128
            or any(not isinstance(value, bytes) or len(value) > MAX_ARTIFACT for value in artifacts.values())
            or sum(len(value) for value in artifacts.values()) > MAX_PACKAGE):
        raise ValueError("Bounded artifact inventory required")
    if not isinstance(attested_producers, Mapping) or not isinstance(allowed_producers, Mapping):
        raise ValueError("Trusted producer policy and attestations required")
    for digest, value in artifacts.items():
        if not isinstance(digest, str) or sha256(value) != digest:
            raise ValueError("Release artifact byte integrity failed")

    referenced = set()

    def artifact(digest):
        if not isinstance(digest, str) or digest not in artifacts:
            raise ValueError("Required release artifact is missing")
        referenced.add(digest)
        return artifacts[digest]

    for field in ARTIFACT_FIELDS:
        artifact(manifest[field])
    target_bytes = artifact(manifest.get("target_manifest_sha256"))
    _targets(target_bytes)
    if not isinstance(current_target_manifest_bytes, bytes) or current_target_manifest_bytes != target_bytes:
        raise ValueError("Deployment targets changed since candidate preparation")
    subject = release_subject_sha256(manifest)
    for name in sorted(REQUIRED_GATES):
        digest = manifest["gates"][name]["evidence_sha256"]
        gate = _json(artifact(digest))
        fields = {"schema", "gate", "status", "subject_sha256", "producer_id",
                  "completed_at", "valid_until", "output_sha256s"}
        if (set(gate) != fields or gate["schema"] != "wealthryse-release-gate-v1"
                or gate["gate"] != name or gate["status"] != "pass"
                or gate["subject_sha256"] != subject):
            raise ValueError(f"Release gate content does not match candidate: {name}")
        producer = attested_producers.get(digest)
        allowed = allowed_producers.get(name)
        if (not isinstance(producer, str) or not producer
                or not isinstance(allowed, frozenset) or producer not in allowed
                or gate["producer_id"] != producer):
            raise ValueError(f"Release gate has no trusted producer attestation: {name}")
        completed, expiry = _timestamp(gate["completed_at"]), _timestamp(gate["valid_until"])
        if not completed <= now < expiry or expiry - completed > timedelta(hours=24):
            raise ValueError(f"Release gate is expired, future-dated or overlong: {name}")
        outputs = gate["output_sha256s"]
        if (not isinstance(outputs, list) or not 1 <= len(outputs) <= 32
                or any(not isinstance(value, str) for value in outputs)
                or len(set(outputs)) != len(outputs) or digest in outputs):
            raise ValueError(f"Invalid release gate output inventory: {name}")
        for output in outputs:
            artifact(output)
    if referenced != set(artifacts):
        raise ValueError("Unreferenced release artifacts are not accepted")
    return {"schema": "wealthryse-release-verification-v1", "manifest_sha256": expected_manifest_sha256,
            "subject_sha256": subject, "verified_at": now.isoformat(),
            "verified_gates": sorted(REQUIRED_GATES), "verified_artifact_count": len(referenced),
            "deployment_authorized": False, "approval_recorded": False}
