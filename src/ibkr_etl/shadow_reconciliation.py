from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Mapping

from .operating_registry import OperatingTarget, load_operating_targets
from .util import canonical_json


SCHEDULED_CAPABILITIES = (
    "daily_ingestion",
    "benchmark_refresh",
    "sector_refresh",
    "analyst_assurance",
    "month_end_reporting",
)
DAILY_SEQUENCE = (
    "source_export",
    "daily_ingestion",
    "benchmark_refresh",
    "sector_refresh",
    "analyst_assurance",
)


@dataclass(frozen=True)
class ShadowJob:
    name: str
    capability: str
    cron: str
    retry_limit: int
    subjects: tuple[str, ...]


def _minute_of_day(cron: str) -> int:
    fields = cron.split()
    if len(fields) != 5 or not fields[0].isdigit() or not fields[1].isdigit():
        raise RuntimeError(
            f"Shadow manifest contains an unsupported cron expression: {cron}"
        )
    minute, hour = int(fields[0]), int(fields[1])
    if minute > 59 or hour > 23:
        raise RuntimeError(
            f"Shadow manifest contains an invalid cron expression: {cron}"
        )
    return hour * 60 + minute


def parse_shadow_jobs(manifest: Mapping[str, Any]) -> list[ShadowJob]:
    if manifest.get("environment") != "azure-container-apps":
        raise RuntimeError("Shadow manifest environment must be azure-container-apps")
    raw_jobs = manifest.get("jobs")
    if not isinstance(raw_jobs, list) or not raw_jobs:
        raise RuntimeError("Shadow manifest requires at least one scheduled job")
    jobs: list[ShadowJob] = []
    seen_names: set[str] = set()
    for raw in raw_jobs:
        if not isinstance(raw, dict):
            raise RuntimeError("Shadow manifest jobs must be objects")
        name = str(raw.get("name", "")).strip()
        capability = str(raw.get("capability", "")).strip()
        cron = str(raw.get("cron", "")).strip()
        if not name or name in seen_names:
            raise RuntimeError("Shadow manifest job names must be non-empty and unique")
        if capability not in {*SCHEDULED_CAPABILITIES, "source_export"}:
            raise RuntimeError(f"Unsupported shadow capability: {capability}")
        if raw.get("trigger") != "Schedule":
            raise RuntimeError(f"Shadow job {name} is not schedule-triggered")
        retry_limit = int(raw.get("retry_limit", -1))
        subjects_raw = raw.get("subjects")
        if not isinstance(subjects_raw, list):
            raise RuntimeError(f"Shadow job {name} requires a subject list")
        subjects = tuple(
            sorted({str(value).strip().upper() for value in subjects_raw})
        )
        if not subjects or any(not value for value in subjects):
            raise RuntimeError(f"Shadow job {name} has an invalid subject list")
        _minute_of_day(cron)
        jobs.append(ShadowJob(name, capability, cron, retry_limit, subjects))
        seen_names.add(name)
    return jobs


def load_shadow_targets(
    connection, *, tenant_id: str, as_of: date
) -> dict[str, list[OperatingTarget]]:
    return {
        capability: load_operating_targets(
            connection,
            tenant_id=tenant_id,
            capability=capability,
            as_of=as_of,
            include_preview=True,
        )
        for capability in SCHEDULED_CAPABILITIES
    }


def reconcile_shadow_plan(
    targets: Mapping[str, Iterable[OperatingTarget]],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    jobs = parse_shadow_jobs(manifest)
    expected: dict[str, set[str]] = {
        capability: {target.subject_id for target in targets.get(capability, ())}
        for capability in SCHEDULED_CAPABILITIES
    }
    expected["source_export"] = {
        target.subject_id
        for target in targets.get("daily_ingestion", ())
        if target.configuration.get("source") == "existing_v11"
    }
    observed = {capability: set() for capability in expected}
    schedule_minutes: dict[str, list[int]] = {
        capability: [] for capability in expected
    }
    mismatches: list[dict[str, Any]] = []
    for job in jobs:
        observed[job.capability].update(job.subjects)
        schedule_minutes[job.capability].append(_minute_of_day(job.cron))
        if job.retry_limit != 0:
            mismatches.append(
                {
                    "code": "nonzero_platform_retry",
                    "job": job.name,
                    "expected": 0,
                    "actual": job.retry_limit,
                }
            )
    for capability in expected:
        if expected[capability] != observed[capability]:
            mismatches.append(
                {
                    "code": "subject_coverage_mismatch",
                    "capability": capability,
                    "expected": sorted(expected[capability]),
                    "actual": sorted(observed[capability]),
                }
            )
        if expected[capability] and not schedule_minutes[capability]:
            mismatches.append(
                {"code": "missing_schedule", "capability": capability}
            )
    prior_minute = -1
    for capability in DAILY_SEQUENCE:
        if not expected[capability]:
            continue
        minute = min(schedule_minutes[capability], default=-1)
        if minute <= prior_minute:
            mismatches.append(
                {
                    "code": "invalid_schedule_sequence",
                    "capability": capability,
                    "minute_of_day_utc": minute,
                    "must_follow_utc_minute": prior_minute,
                }
            )
        prior_minute = max(prior_minute, minute)
    all_targets = [target for values in targets.values() for target in values]
    active_subjects = sorted({
        target.subject_id
        for target in all_targets
        if target.operating_state == "active"
    })
    preview_subjects = sorted(
        {
            target.subject_id
            for target in all_targets
            if target.operating_state == "preview"
        }
    )
    evidence = {
        "manifest_sha256": hashlib.sha256(
            canonical_json(dict(manifest)).encode("utf-8")
        ).hexdigest(),
        "registry_sha256": hashlib.sha256(
            canonical_json(
                {
                    capability: [
                        {
                            "subject_id": target.subject_id,
                            "state": target.operating_state,
                            "configuration_sha256": target.configuration_sha256,
                        }
                        for target in sorted(values, key=lambda item: item.subject_id)
                    ]
                    for capability, values in sorted(targets.items())
                }
            ).encode("utf-8")
        ).hexdigest(),
        "expected_coverage": {
            capability: sorted(subjects) for capability, subjects in expected.items()
        },
        "observed_coverage": {
            capability: sorted(subjects) for capability, subjects in observed.items()
        },
        "jobs": [
            {
                "name": job.name,
                "capability": job.capability,
                "cron": job.cron,
                "retry_limit": job.retry_limit,
                "subjects": list(job.subjects),
            }
            for job in jobs
        ],
        "active_subjects": active_subjects,
        "preview_subjects": preview_subjects,
        "planned_capability_count": sum(len(values) for values in expected.values()),
        "mismatches": mismatches,
    }
    evidence["status"] = "pass" if not mismatches else "fail"
    return evidence


def record_shadow_reconciliation(
    connection,
    *,
    tenant_id: str,
    run_date: date,
    evidence: Mapping[str, Any],
) -> str:
    reconciliation_id = str(
        uuid.uuid5(
            uuid.UUID("c6462d1e-63bb-4ae8-a0ac-0cfb9dddb738"),
            (
                f"{tenant_id}:{run_date.isoformat()}:"
                f"{evidence['manifest_sha256']}:{evidence['registry_sha256']}"
            ),
        )
    )
    connection.execute(
        """
        INSERT INTO institutional_shadow_reconciliations(
            reconciliation_id, tenant_id, run_date,
            authoritative_manifest_sha256, registry_sha256, status,
            active_subject_count, preview_subject_count,
            planned_capability_count, mismatch_count,
            evidence_json, mismatches_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?::jsonb, ?::jsonb)
        ON CONFLICT (tenant_id, run_date, authoritative_manifest_sha256, registry_sha256)
        DO NOTHING
        """,
        (
            reconciliation_id,
            tenant_id,
            run_date.isoformat(),
            evidence["manifest_sha256"],
            evidence["registry_sha256"],
            evidence["status"],
            len(evidence["active_subjects"]),
            len(evidence["preview_subjects"]),
            int(evidence["planned_capability_count"]),
            len(evidence["mismatches"]),
            canonical_json(dict(evidence)),
            canonical_json(list(evidence["mismatches"])),
        ),
    )
    return reconciliation_id
