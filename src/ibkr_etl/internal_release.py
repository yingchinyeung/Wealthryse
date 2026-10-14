from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


INTERNAL_RELEASE_SCOPE = "internal_shareholder"
ALLOWED_INTERNAL_BLOCKERS = frozenset(
    {
        "primary_benchmark:not_licensed_for_publication",
        "secondary_benchmark:not_licensed_for_publication",
        "sector_allocation:not_licensed_for_publication",
    }
)
_BENCHMARK_LICENSE_BLOCKER = re.compile(
    r"(?:primary|secondary(?:_[1-9][0-9]*)?)_benchmark:not_licensed_for_publication\Z"
)


class InternalReleaseError(ValueError):
    pass


@dataclass(frozen=True)
class InternalReleaseApproval:
    snapshot_id: str
    subject_id: str
    period_end: str
    blockers: tuple[str, ...]
    generation_run_ids: tuple[int, ...]


def approve_internal_release(
    snapshot: Mapping[str, Any],
    report_runs: Sequence[Mapping[str, Any]],
    *,
    user_acceptance_recorded: bool,
) -> InternalReleaseApproval:
    if str(snapshot.get("status")) not in {"frozen", "approved"}:
        raise InternalReleaseError("Internal release requires a frozen snapshot")
    if str(snapshot.get("quality_status")) == "fail":
        raise InternalReleaseError("A failing snapshot cannot be released internally")
    if not user_acceptance_recorded:
        raise InternalReleaseError("Internal release requires recorded user acceptance")

    raw_blockers = snapshot.get("publication_blockers_json") or "[]"
    blockers = tuple(json.loads(raw_blockers) if isinstance(raw_blockers, str) else raw_blockers)
    unexpected = {
        blocker for blocker in blockers
        if blocker not in ALLOWED_INTERNAL_BLOCKERS
        and not _BENCHMARK_LICENSE_BLOCKER.fullmatch(blocker)
    }
    if unexpected:
        raise InternalReleaseError(
            f"Internal release has non-licensing blockers: {sorted(unexpected)}"
        )
    # The factsheet, plus a deck only where one was built before decks were retired.
    kinds = [str(row.get("report_type")) for row in report_runs]
    if len(kinds) != len(set(kinds)) or "factsheet" not in kinds or not set(kinds) <= {"factsheet", "presentation"}:
        raise InternalReleaseError("Internal release requires one factsheet, and at most one presentation")
    for row in report_runs:
        if str(row.get("snapshot_id")) != str(snapshot.get("snapshot_id")):
            raise InternalReleaseError("Every artifact must reference the approved snapshot")
        if str(row.get("status")) not in {"awaiting_approval", "released_internal"}:
            raise InternalReleaseError("An artifact is not ready for internal release")
        digest = str(row.get("output_sha256") or "")
        if len(digest) != 64:
            raise InternalReleaseError("An artifact is missing its SHA-256 digest")

    return InternalReleaseApproval(
        snapshot_id=str(snapshot["snapshot_id"]),
        subject_id=str(snapshot["subject_id"]),
        period_end=str(snapshot["period_end"]),
        blockers=tuple(sorted(blockers)),
        generation_run_ids=tuple(sorted(int(row["generation_run_id"]) for row in report_runs)),
    )
