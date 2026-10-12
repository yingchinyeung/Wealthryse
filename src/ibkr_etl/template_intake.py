from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from .template_upload import (
    ALPHA_WORLD_SHARED_TEMPLATE_SUBJECTS,
    normalize_strategy_identity,
    validate_strategy_name,
)


TEMPLATE_ASSET_NAMESPACE = uuid.UUID("e5681158-614e-413d-a15e-624080b3fd55")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_CONTENT_TYPES = {
    ("factsheet", "template"): "application/pdf",
    ("factsheet", "completed_example"): "application/pdf",
    (
        "presentation",
        "template",
    ): "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    (
        "presentation",
        "completed_example",
    ): "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


@dataclass(frozen=True)
class TemplateAsset:
    report_type: str
    asset_role: str
    version_label: str
    container_name: str
    blob_name: str
    content_type: str
    sha256: str
    byte_count: int
    metadata: dict[str, Any]


def parse_template_manifest(
    content: bytes,
    *,
    subject_id: str,
    container_name: str,
) -> tuple[TemplateAsset, ...]:
    """Validate one bounded, private report-template intake manifest."""
    if len(content) > 256 * 1024:
        raise RuntimeError("Template intake manifest exceeds 256 KiB")
    try:
        manifest = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("Template intake manifest is not valid UTF-8 JSON") from error
    if not isinstance(manifest, dict) or set(manifest) != {"subject_id", "assets"}:
        raise RuntimeError("Template intake manifest must contain subject_id and assets")
    if str(manifest["subject_id"]).strip() != subject_id:
        raise RuntimeError("Template intake manifest subject does not match the job")
    raw_assets = manifest["assets"]
    if not isinstance(raw_assets, list) or not 3 <= len(raw_assets) <= 8:
        raise RuntimeError("Template intake manifest must contain between 3 and 8 assets")

    expected_keys = {
        "report_type",
        "asset_role",
        "version_label",
        "container_name",
        "blob_name",
        "content_type",
        "sha256",
        "byte_count",
        "metadata",
    }
    prefix = f"templates/{subject_id}/intake/"
    assets: list[TemplateAsset] = []
    identities: set[tuple[str, str]] = set()
    blob_names: set[str] = set()
    for raw in raw_assets:
        if not isinstance(raw, dict) or set(raw) != expected_keys:
            raise RuntimeError("Each template asset must contain only approved fields")
        report_type = str(raw["report_type"]).strip()
        asset_role = str(raw["asset_role"]).strip()
        identity = (report_type, asset_role)
        if identity not in ALLOWED_CONTENT_TYPES:
            raise RuntimeError("Template asset has an unsupported type or role")
        if identity in identities:
            raise RuntimeError("Template manifest contains a duplicate type and role")
        identities.add(identity)
        if str(raw["container_name"]).strip() != container_name:
            raise RuntimeError("Template asset is outside the configured private container")
        blob_name = str(raw["blob_name"]).strip()
        if not blob_name.startswith(prefix) or ".." in blob_name.split("/"):
            raise RuntimeError("Template asset is outside the subject intake prefix")
        if blob_name in blob_names:
            raise RuntimeError("Template manifest contains a duplicate blob")
        blob_names.add(blob_name)
        content_type = str(raw["content_type"]).strip().lower()
        if content_type != ALLOWED_CONTENT_TYPES[identity]:
            raise RuntimeError("Template asset content type does not match its role")
        digest = str(raw["sha256"]).strip().lower()
        if not SHA256_PATTERN.fullmatch(digest):
            raise RuntimeError("Template asset has an invalid SHA-256 digest")
        try:
            byte_count = int(raw["byte_count"])
        except (TypeError, ValueError) as error:
            raise RuntimeError("Template asset byte count is invalid") from error
        if not 0 < byte_count <= 50 * 1024 * 1024:
            raise RuntimeError("Template asset exceeds the 50 MiB intake limit")
        version_label = str(raw["version_label"]).strip()
        if not version_label or len(version_label) > 160:
            raise RuntimeError("Template asset version label is invalid")
        metadata = raw["metadata"]
        if not isinstance(metadata, dict):
            raise RuntimeError("Template asset metadata must be an object")
        contract = metadata.get("presentation_contract")
        if identity == ("presentation", "template") and contract is not None:
            required_contract_keys = {
                "schema_version",
                "strategy_name",
                "strategy_identity_sha256",
                "visible_text_sha256",
                "strategy_marker_count",
                "slide_count",
                "layout_policy",
                "source_template_sha256",
                "owner_approval_id",
                "owner_approval_scope_sha256",
                "identity_source",
            }
            optional_contract_keys = {
                "identity_validation",
                "identity_exception_id",
                "identity_exception_subject_id",
                "identity_exception_scope",
            }
            if (
                not isinstance(contract, dict)
                or not required_contract_keys.issubset(contract)
                or set(contract) - required_contract_keys - optional_contract_keys
            ):
                raise RuntimeError("Presentation identity contract fields are invalid")
            identity_validation = str(
                contract.get("identity_validation") or "exact_strategy_title"
            )
            exception_keys = {
                "identity_exception_id",
                "identity_exception_subject_id",
                "identity_exception_scope",
            }
            if identity_validation == "shared_family_template_exception":
                if not exception_keys.issubset(contract):
                    raise RuntimeError(
                        "Presentation identity exception fields are incomplete"
                    )
                if (
                    contract["identity_exception_id"]
                    != "alpha_world_shared_family_template_v1"
                    or contract["identity_exception_subject_id"] != subject_id
                    or subject_id not in ALPHA_WORLD_SHARED_TEMPLATE_SUBJECTS
                    or contract["identity_exception_scope"]
                    != sorted(ALPHA_WORLD_SHARED_TEMPLATE_SUBJECTS)
                ):
                    raise RuntimeError(
                        "Presentation identity exception is outside its approved scope"
                    )
            elif identity_validation == "exact_strategy_title":
                if exception_keys.intersection(contract):
                    raise RuntimeError(
                        "Presentation identity exception fields are not permitted"
                    )
            else:
                raise RuntimeError("Presentation identity validation mode is invalid")
            strategy_name = validate_strategy_name(str(contract["strategy_name"]))
            identity_digest = hashlib.sha256(
                normalize_strategy_identity(strategy_name).encode("utf-8")
            ).hexdigest()
            if contract["schema_version"] != 1:
                raise RuntimeError("Presentation identity contract version is unsupported")
            if contract["strategy_identity_sha256"] != identity_digest:
                raise RuntimeError("Presentation strategy identity digest is invalid")
            if not SHA256_PATTERN.fullmatch(str(contract["visible_text_sha256"])):
                raise RuntimeError("Presentation visible-text digest is invalid")
            if contract["source_template_sha256"] != digest:
                raise RuntimeError("Presentation identity contract is bound to another file")
            try:
                uuid.UUID(str(contract["owner_approval_id"]))
            except (TypeError, ValueError) as error:
                raise RuntimeError("Presentation owner approval identifier is invalid") from error
            if not SHA256_PATTERN.fullmatch(
                str(contract["owner_approval_scope_sha256"])
            ):
                raise RuntimeError("Presentation owner approval scope digest is invalid")
            if contract["identity_source"] != "owner_approved_subject_display_name":
                raise RuntimeError("Presentation identity source is not owner controlled")
            if contract["layout_policy"] not in {
                "platform_standard",
                "subject_template",
            }:
                raise RuntimeError("Presentation layout policy is invalid")
            minimum_marker_count = (
                0 if identity_validation == "shared_family_template_exception" else 1
            )
            if not minimum_marker_count <= int(contract["strategy_marker_count"]) <= 100:
                raise RuntimeError("Presentation strategy marker count is invalid")
            if not 1 <= int(contract["slide_count"]) <= 100:
                raise RuntimeError("Presentation slide count is invalid")
        assets.append(
            TemplateAsset(
                report_type=report_type,
                asset_role=asset_role,
                version_label=version_label,
                container_name=container_name,
                blob_name=blob_name,
                content_type=content_type,
                sha256=digest,
                byte_count=byte_count,
                metadata=metadata,
            )
        )
    required = {
        ("factsheet", "template"),
        ("presentation", "template"),
        ("factsheet", "completed_example"),
    }
    if not required.issubset(identities):
        raise RuntimeError(
            "A factsheet template, presentation template and completed factsheet "
            "example are all required"
        )
    return tuple(assets)


def verify_template_assets(
    assets: tuple[TemplateAsset, ...],
    *,
    download: Callable[[str, str], bytes],
) -> None:
    """Verify every registered byte against the manifest before database writes."""
    for asset in assets:
        content = download(asset.container_name, asset.blob_name)
        if len(content) != asset.byte_count:
            raise RuntimeError(f"Template asset size mismatch: {asset.blob_name}")
        if hashlib.sha256(content).hexdigest() != asset.sha256:
            raise RuntimeError(f"Template asset hash mismatch: {asset.blob_name}")


def _asset_id(tenant_id: str, subject_id: str, asset: TemplateAsset) -> str:
    return str(
        uuid.uuid5(
            TEMPLATE_ASSET_NAMESPACE,
            ":".join(
                (
                    tenant_id,
                    subject_id,
                    asset.report_type,
                    asset.asset_role,
                    asset.version_label,
                    asset.sha256,
                )
            ),
        )
    )


def register_template_assets(
    connection,
    *,
    tenant_id: str,
    subject_id: str,
    assets: tuple[TemplateAsset, ...],
) -> dict[str, Any]:
    """Atomically register verified assets, pass the gate and advance intake."""
    subject = connection.execute(
        "SELECT lifecycle_status FROM platform_subjects WHERE tenant_id = ? AND subject_id = ?",
        (tenant_id, subject_id),
    ).fetchone()
    if subject is None:
        raise RuntimeError("Template intake subject is not registered")
    case = connection.execute(
        """
        SELECT case_id, stage FROM account_onboarding_cases
        WHERE tenant_id = ? AND target_subject_id = ?
        """,
        (tenant_id, subject_id),
    ).fetchone()
    if case is None:
        raise RuntimeError("Template intake onboarding case is missing")
    if case["stage"] not in {"template_intake", "reconciliation"}:
        raise RuntimeError("Template intake cannot run at the current onboarding stage")

    evidence_assets: list[dict[str, Any]] = []
    for asset in assets:
        connection.execute(
            """
            UPDATE subject_report_template_assets
            SET is_current = false,
                approval_status = CASE
                    WHEN approval_status = 'active' THEN 'superseded'
                    ELSE approval_status
                END,
                updated_at = now()
            WHERE tenant_id = ? AND subject_id = ? AND report_type = ?
              AND asset_role = ? AND is_current
              AND version_label <> ?
            """,
            (
                tenant_id,
                subject_id,
                asset.report_type,
                asset.asset_role,
                asset.version_label,
            ),
        )
        connection.execute(
            """
            INSERT INTO subject_report_template_assets(
                template_asset_id, tenant_id, subject_id, report_type, asset_role,
                version_label, container_name, blob_name, content_type, sha256,
                byte_count, approval_status, is_current, metadata_json,
                registered_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'validated', true, ?::jsonb,
                now(), now()
            )
            ON CONFLICT (tenant_id, subject_id, report_type, asset_role, version_label)
            DO UPDATE SET
                container_name = EXCLUDED.container_name,
                blob_name = EXCLUDED.blob_name,
                content_type = EXCLUDED.content_type,
                sha256 = EXCLUDED.sha256,
                byte_count = EXCLUDED.byte_count,
                approval_status = 'validated',
                is_current = true,
                metadata_json = EXCLUDED.metadata_json,
                updated_at = now()
            """,
            (
                _asset_id(tenant_id, subject_id, asset),
                tenant_id,
                subject_id,
                asset.report_type,
                asset.asset_role,
                asset.version_label,
                asset.container_name,
                asset.blob_name,
                asset.content_type,
                asset.sha256,
                asset.byte_count,
                json.dumps(asset.metadata, sort_keys=True),
            ),
        )
        evidence_assets.append(
            {
                "report_type": asset.report_type,
                "asset_role": asset.asset_role,
                "version_label": asset.version_label,
                "blob_name": asset.blob_name,
                "sha256": asset.sha256,
                "byte_count": asset.byte_count,
            }
        )

    evidence = {
        "status": "validated",
        "asset_count": len(evidence_assets),
        "assets": evidence_assets,
    }
    connection.execute(
        """
        UPDATE account_onboarding_checks
        SET status = 'pass',
            message = ?,
            evidence_json = ?::jsonb,
            checked_at = now()
        WHERE tenant_id = ? AND case_id = ?
          AND check_id = 'report_documents_validated'
        """,
        (
            "All three mandatory report documents were validated and stored.",
            json.dumps(evidence, sort_keys=True),
            tenant_id,
            case["case_id"],
        ),
    )
    connection.execute(
        """
        UPDATE account_onboarding_cases
        SET stage = 'reconciliation', status = 'in_progress',
            configuration_json = configuration_json || ?::jsonb,
            updated_at = now()
        WHERE tenant_id = ? AND case_id = ?
        """,
        (
            json.dumps({"template_intake": evidence}, sort_keys=True),
            tenant_id,
            case["case_id"],
        ),
    )
    return evidence
