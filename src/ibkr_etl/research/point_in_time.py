"""What was known on a given date.

A research result is only honest if every input is what the platform knew at the moment the
decision would have been made. Three pieces make that hold:

* :func:`as_known` – from versioned records (a key, the date the value is for, the time it
  became known, the value, and whether it was withdrawn), the latest version of each key known
  at a cut-off. This is how the five silver tables with "as known on" history are read, and how
  bronze is replayed by ingestion time for tables without history (positions, instruments,
  market prices), as the Medallion design says.
* :func:`knowledge_time` – when a value for a date becomes known, from a publication lag and a
  cut-off time of day (an IBKR statement for Friday arrives Saturday at 08:20 UTC, say).
* :class:`PointInTimeView` – dated series wrapped so a backtest's signal can read only what was
  known at the decision time; reading later data raises :class:`LookaheadError`.

Pure functions; no database, storage or network access.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Hashable, Iterable, Mapping, Sequence


class LookaheadError(LookupError):
    """A read asked for data that was not known yet at the decision time."""


@dataclass(frozen=True)
class Version:
    """One version of a record: its value for ``effective_date`` as known from ``known_at``."""

    key: Hashable
    effective_date: str
    known_at: datetime
    value: Any
    withdrawn: bool = False


def _utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError("known_at must carry a time zone")
    return moment.astimezone(timezone.utc)


def as_known(versions: Iterable[Version], known_on: datetime) -> dict[Hashable, Version]:
    """The latest version of each key known at ``known_on``; withdrawn keys are left out.

    Ties on ``known_at`` keep the version that came later in the input, matching an
    append-only log where a re-sent record follows the original.
    """
    cutoff = _utc(known_on)
    latest: dict[Hashable, tuple[datetime, int, Version]] = {}
    for order, version in enumerate(versions):
        moment = _utc(version.known_at)
        if moment > cutoff:
            continue
        held = latest.get(version.key)
        if held is None or (moment, order) >= (held[0], held[1]):
            latest[version.key] = (moment, order, version)
    return {key: item[2] for key, item in latest.items() if not item[2].withdrawn}


def knowledge_time(effective_date: str, *, lag_days: int = 1, cutoff: time = time(8, 20),
                   weekends: bool = True) -> datetime:
    """When a value for ``effective_date`` becomes known (UTC).

    ``lag_days`` days after the date, at ``cutoff``. With ``weekends=False`` the lag counts
    weekdays only, for sources that publish on business days.
    """
    day = date.fromisoformat(effective_date)
    remaining = lag_days
    while remaining > 0:
        day += timedelta(days=1)
        if weekends or day.weekday() < 5:
            remaining -= 1
    return datetime.combine(day, cutoff, tzinfo=timezone.utc)


class PointInTimeView:
    """Dated series that can be read only up to a decision time.

    ``series`` maps a name to {iso date: value}. Each value becomes known at
    ``knowledge_time(date, **timing[name])`` (default: the same day's close, lag 0 at 23:59
    UTC, which suits market closes). :meth:`at` returns a copy bound to a decision time;
    reading through it never returns a value known later.
    """

    def __init__(self, series: Mapping[str, Mapping[str, float]], *,
                 timing: Mapping[str, Mapping[str, Any]] | None = None,
                 decision_time: datetime | None = None) -> None:
        self._series = {name: dict(sorted(values.items())) for name, values in series.items()}
        self._timing = {name: dict(spec) for name, spec in (timing or {}).items()}
        self._dates = {name: list(values) for name, values in self._series.items()}
        self._known = {
            name: [knowledge_time(day, **self._timing_for(name)) for day in days]
            for name, days in self._dates.items()
        }
        self._decision = _utc(decision_time) if decision_time else None

    def _timing_for(self, name: str) -> dict[str, Any]:
        return self._timing.get(name, {"lag_days": 0, "cutoff": time(23, 59)})

    def at(self, decision_time: datetime) -> "PointInTimeView":
        view = object.__new__(PointInTimeView)
        view._series, view._timing, view._dates, view._known = (
            self._series, self._timing, self._dates, self._known)
        view._decision = _utc(decision_time)
        return view

    @property
    def names(self) -> list[str]:
        return list(self._series)

    def _limit(self, name: str) -> int:
        if name not in self._series:
            raise KeyError(name)
        if self._decision is None:
            return len(self._dates[name])
        return bisect.bisect_right(self._known[name], self._decision)

    def history(self, name: str, *, last: int | None = None) -> list[tuple[str, float]]:
        """(date, value) pairs known at the decision time, oldest first."""
        end = self._limit(name)
        start = 0 if last is None else max(0, end - last)
        days = self._dates[name]
        return [(days[i], self._series[name][days[i]]) for i in range(start, end)]

    def latest(self, name: str) -> tuple[str, float] | None:
        end = self._limit(name)
        if end == 0:
            return None
        day = self._dates[name][end - 1]
        return day, self._series[name][day]

    def value(self, name: str, day: str) -> float:
        """The value for ``day``; raises LookaheadError if it was not known yet."""
        if day not in self._series.get(name, {}):
            raise KeyError((name, day))
        index = self._dates[name].index(day)
        if index >= self._limit(name):
            raise LookaheadError(f"{name} for {day} was not known at {self._decision.isoformat()}")
        return self._series[name][day]


def replay_order(records: Sequence[Mapping[str, Any]], *, key_fields: Sequence[str],
                 date_field: str, ingested_field: str, value_fields: Sequence[str]) -> list[Version]:
    """Bronze rows turned into versions, in ingestion order, for :func:`as_known`.

    For tables without "as known on" history, each bronze row's ingestion time is when its
    value became known. ``ingested_field`` must hold a timezone-aware datetime.
    """
    versions = [
        Version(
            key=tuple(row[field] for field in key_fields),
            effective_date=str(row[date_field]),
            known_at=row[ingested_field],
            value={field: row[field] for field in value_fields},
            withdrawn=bool(row.get("withdrawn", False)),
        )
        for row in records
    ]
    return sorted(versions, key=lambda version: _utc(version.known_at))
