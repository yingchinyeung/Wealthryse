"""Conservative aggregation of account-level health without hiding failures."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Mapping, Sequence


def aggregate_health(latest_run: Mapping[str, Any] | None,
                     assurance_rows: Sequence[Mapping[str, Any]], *, today: date) -> str:
    if not latest_run or latest_run.get("status") != "success":
        return "fail"
    if any(row.get("status") == "fail" for row in assurance_rows):
        return "fail"
    if not assurance_rows:
        return "warn"
    for row in assurance_rows:
        if row.get("status") != "pass":
            return "warn"
        try:
            observed = date.fromisoformat(str(row.get("assurance_date"))[:10])
        except (TypeError, ValueError):
            return "warn"
        # Allow the previous daily run to remain valid until the next cycle.
        if observed < today - timedelta(days=1) or observed > today:
            return "warn"
    return "pass"
