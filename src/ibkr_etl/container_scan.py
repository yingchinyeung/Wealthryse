"""Offline scan-content assessment; hashes and scanner output are not authority.

The trusted build adapter must authenticate the scanner and independently bind
manifest/configuration digests to registry bytes. This module neither obtains
credentials nor grants deployment approval. No suppression/exception is implied.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re


MAX_BYTES = 32 * 1024 * 1024
SEVERITIES = frozenset(("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"))
REQUIRED_TYPES = frozenset(("debian", "python-pkg", "node-pkg"))


def _document(data):
    if not isinstance(data, bytes) or not 1 <= len(data) <= MAX_BYTES:
        raise ValueError("Bounded scan artifact bytes required")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate scan JSON key")
            result[key] = value
        return result

    def invalid(_):
        raise ValueError("Non-finite scan JSON")

    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=unique, parse_constant=invalid)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("Invalid scan JSON") from None
    if not isinstance(value, dict):
        raise ValueError("Scan artifact must be an object")
    return value


def _time(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.utcoffset() is None:
            raise ValueError("Timezone required")
        return result.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        raise ValueError("Aware scan timestamp required") from None


def assess_container_scan(*, report_bytes, sbom_bytes, image_digest, config_digest,
                          scanner_version, now, required_types=REQUIRED_TYPES):
    """Assess raw outputs against trusted image identities and scanner policy.

    Scanner success alone is insufficient: require OS/Python/Node coverage,
    package inventories, consistent SBOM identity/coverage and recent output.
    Critical/high/unknown findings block; no fixed-version filter or allowlist.
    required_types is trusted build policy, not inferred from missing results.
    A dedicated Python-only risk image does not contain a Node runtime; its
    independently reviewed build policy still requires OS and Python coverage.
    """
    for value in (image_digest, config_digest):
        if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
            raise ValueError("Trusted immutable manifest/config identities required")
    if not isinstance(scanner_version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", scanner_version):
        raise ValueError("Trusted scanner version required")
    if (not isinstance(required_types, frozenset)
            or not frozenset(("debian", "python-pkg")) <= required_types <= REQUIRED_TYPES):
        raise ValueError("Trusted build scanner-coverage policy required")
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ValueError("Aware trusted server time required")
    now = now.astimezone(timezone.utc)
    report, sbom = _document(report_bytes), _document(sbom_bytes)
    metadata = report.get("Metadata")
    if (report.get("SchemaVersion") != 2 or report.get("ArtifactType") != "container_image"
            or not isinstance(report.get("Trivy"), dict)
            or report["Trivy"].get("Version") != scanner_version
            or not isinstance(metadata, dict) or metadata.get("ImageID") != config_digest
            or not isinstance(metadata.get("OS"), dict)
            or metadata["OS"].get("Family") != "debian" or not metadata["OS"].get("Name")
            or metadata["OS"].get("EOSL") not in (None, False)):
        raise ValueError("Unsupported, EOL or mismatched container scan identity")
    if not isinstance(sbom.get("metadata"), dict):
        raise ValueError("SBOM metadata required")
    for stamp in (report.get("CreatedAt"), sbom["metadata"].get("timestamp")):
        if not now - timedelta(hours=24) <= _time(stamp) <= now:
            raise ValueError("Stale or future-dated scanner output")
    results = report.get("Results")
    if not isinstance(results, list) or not 1 <= len(results) <= 128:
        raise ValueError("Complete scanner result inventory required")
    counts = dict.fromkeys(sorted(SEVERITIES), 0)
    coverage, packages, seen_results, package_count = set(), set(), set(), 0
    for result in results:
        if not isinstance(result, dict) or not isinstance(result.get("Target"), str) or not result["Target"]:
            raise ValueError("Invalid scanner target")
        package_type = result.get("Type")
        expected_class = "os-pkgs" if package_type == "debian" else "lang-pkgs"
        key = (result["Target"], package_type)
        if (not isinstance(package_type, str) or package_type not in REQUIRED_TYPES
                or result.get("Class") != expected_class or key in seen_results):
            raise ValueError("Unsupported or duplicate scanner target")
        seen_results.add(key)
        coverage.add(package_type)
        inventory = result.get("Packages")
        if not isinstance(inventory, list) or not 1 <= len(inventory) <= 100000:
            raise ValueError("Scanner package inventory is missing")
        package_count += len(inventory)
        for package in inventory:
            if not isinstance(package, dict) or not isinstance(package.get("Identifier"), dict):
                raise ValueError("Invalid scanned package")
            purl = package["Identifier"].get("PURL")
            if not isinstance(purl, str) or not purl.startswith("pkg:"):
                raise ValueError("Scanned package has no PURL")
            packages.add(purl)
        findings = result.get("Vulnerabilities", [])  # Trivy omits an empty list.
        if not isinstance(findings, list) or len(findings) > 100000:
            raise ValueError("Invalid vulnerability inventory")
        for finding in findings:
            if (not isinstance(finding, dict) or not isinstance(finding.get("Severity"), str)
                    or finding["Severity"] not in SEVERITIES
                    or any(not isinstance(finding.get(k), str) or not finding[k]
                           for k in ("VulnerabilityID", "PkgName", "InstalledVersion"))):
                raise ValueError("Unclassifiable scanner finding")
            counts[finding["Severity"]] += 1
    if coverage != required_types:
        raise ValueError("Container scanner coverage differs from reviewed build policy")
    if sbom.get("bomFormat") != "CycloneDX" or sbom.get("specVersion") not in ("1.5", "1.6", "1.7"):
        raise ValueError("Supported CycloneDX SBOM required")
    bom_metadata = sbom.get("metadata")
    if not isinstance(bom_metadata, dict) or not isinstance(bom_metadata.get("component"), dict):
        raise ValueError("SBOM image metadata required")
    properties = bom_metadata["component"].get("properties", [])
    if not isinstance(properties, list) or any(not isinstance(p, dict) for p in properties):
        raise ValueError("Invalid SBOM image properties")
    identities = [p.get("value") for p in properties if p.get("name") == "aquasecurity:trivy:ImageID"]
    if identities != [config_digest]:
        raise ValueError("SBOM and scanned image identity differ")
    components = sbom.get("components")
    if not isinstance(components, list) or not 1 <= len(components) <= 100000:
        raise ValueError("SBOM package inventory required")
    if any(not isinstance(c, dict) for c in components):
        raise ValueError("Invalid SBOM package")
    bom_purls = {c["purl"] for c in components if isinstance(c.get("purl"), str)}
    if not packages <= bom_purls:
        raise ValueError("SBOM does not cover scanned packages")
    blocked = sum(counts[s] for s in ("CRITICAL", "HIGH", "UNKNOWN"))
    return {"schema": "wealthryse-container-scan-assessment-v1",
            "image_digest": image_digest, "config_digest": config_digest,
            "scanner_version": scanner_version, "status": "blocked" if blocked else "pass",
            "counts": counts, "blocking_findings": blocked,
            "scanned_packages": package_count, "unique_package_purls": len(packages),
            "sbom_components": len(components), "covered_types": sorted(coverage),
            "report_sha256": hashlib.sha256(report_bytes).hexdigest(),
            "sbom_sha256": hashlib.sha256(sbom_bytes).hexdigest(),
            "deployment_authorized": False}
