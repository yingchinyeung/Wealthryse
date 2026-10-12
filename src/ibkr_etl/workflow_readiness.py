"""Pure readiness policy for the dependency-driven daily workflow.

This policy does not dispatch work, retry Flex, or infer freshness from exit codes.
The future durable dispatcher must persist evidence and call this under its lease.
Expected dates are supplied by the approved market/calendar policy, not today().
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re
from typing import Mapping


PREREQUISITES = {
    "ingestion": (),
    "reconciliation": ("ingestion",),
    "benchmarks_fx": ("reconciliation",),
    "derived": ("reconciliation", "benchmarks_fx"),
    "risk": ("derived", "benchmarks_fx"),
    "reports": ("risk",),
    "assurance": ("reports",),
}


@dataclass(frozen=True)
class StageEvidence:
    subject_id: str
    stage: str
    status: str
    validated: bool
    through_date: date
    output_sha256: str
    dependency_hashes: Mapping[str, str]


def readiness(stage: str, *, subject_id: str, expected_date: date,
              evidence: Mapping[str, StageEvidence],
              expected_dates: Mapping[str, date] | None = None) -> tuple[bool, tuple[str, ...]]:
    """Reject missing, stale, failed, mismatched and correction-invalidated evidence."""
    if stage not in PREREQUISITES or type(expected_date) is not date:
        raise ValueError("Known stage and explicit cycle cutoff required")
    needed: set[str] = set()
    def visit(name):
        for parent in PREREQUISITES[name]:
            if parent not in needed:
                needed.add(parent)
                visit(parent)
    visit(stage)
    # A reviewed source-specific policy may require different observation dates
    # for ingestion and an exchange closed for a long holiday. Never derive the
    # expectation from the newest available row or accept a partial policy.
    if expected_dates is not None:
        if (set(expected_dates)!=needed or any(type(day) is not date or day>expected_date
                for day in expected_dates.values())):
            raise ValueError("Exact non-future prerequisite date policy required")
    reasons: list[str] = []
    checked: set[str] = set()

    def validate(name: str) -> None:
        if name in checked:
            return
        checked.add(name)
        item = evidence.get(name)
        if item is None:
            reasons.append(f"{name}: missing completion evidence")
            return
        if item.subject_id != subject_id or item.stage != name:
            reasons.append(f"{name}: subject/stage mismatch")
        if item.status not in {"succeeded", "unchanged"} or item.validated is not True:
            reasons.append(f"{name}: not validated")
        required=expected_dates[name] if expected_dates is not None else expected_date
        if type(item.through_date) is not date:
            reasons.append(f"{name}: invalid observation date")
        elif item.through_date > expected_date:
            reasons.append(f"{name}: future data")
        elif item.through_date < required:
            reasons.append(f"{name}: stale data")
        if not re.fullmatch(r"[0-9a-f]{64}", item.output_sha256):
            reasons.append(f"{name}: invalid output fingerprint")
        for parent in PREREQUISITES[name]:
            validate(parent)
            upstream = evidence.get(parent)
            if upstream is not None and item.dependency_hashes.get(parent) != upstream.output_sha256:
                reasons.append(f"{name}: {parent} changed; recalculate")

    for dependency in PREREQUISITES[stage]:
        validate(dependency)
    return not reasons, tuple(reasons)
