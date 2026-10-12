"""Fail-closed dated FX selection shared by analytical consumers.

An explicit invalid observation is not the same as an absent observation. Keep
it in history so it cannot silently expose an older positive quote. No new
staleness policy or replacement market rate is imposed here.
"""
from datetime import date
from bisect import bisect_right
import math


class InvalidFxObservation(ValueError):
    """Latest eligible FX evidence is unusable or ambiguous; do not publish."""


def latest_fx_quote(values, on_date=None):
    """Select from ascending ISO-date history (sorted once by each consumer)."""
    cutoff = date.fromisoformat(str(on_date)).isoformat() if on_date is not None else None
    index = len(values) - 1 if cutoff is None else bisect_right(values, cutoff, key=lambda item: str(item[0])) - 1
    if index < 0:
        return None
    observed, value = values[index]
    latest = date.fromisoformat(str(observed))
    if index > 0 and str(values[index - 1][0]) == str(observed):
        raise InvalidFxObservation("Ambiguous latest FX observation")
    try:
        rate = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise InvalidFxObservation("Invalid latest FX observation") from error
    if not math.isfinite(rate) or rate <= 0:
        raise InvalidFxObservation("Invalid latest FX observation")
    return rate, latest.isoformat()
