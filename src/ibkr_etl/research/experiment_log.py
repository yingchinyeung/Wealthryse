"""An append-only log of every research trial.

Trying many versions of an idea makes luck look like skill. The deflated Sharpe ratio corrects
for that, but only if every attempt is counted, including the ones that failed. This log
records each trial as one JSON line: the idea family, the hypothesis in words, the parameters,
a fingerprint of the point-in-time data it used, the code version and its results. A trial's
id is the sha256 of that content, so re-recording the same trial is harmless and changing a
recorded trial is refused.

The store is pluggable: :class:`MemoryStore` here, and the research job's store for the
``research-experiments`` container, which writes an append blob under the research identity.
:meth:`ExperimentLog.family_statistics` gives the trial count and the variance of trial
Sharpe ratios that the deflated Sharpe ratio needs.
"""
from __future__ import annotations

import hashlib
import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Protocol

CONTAINER = "research-experiments"
LOG_PATH = "experiments/log.jsonl"


class Store(Protocol):
    def append(self, line: str) -> None: ...
    def lines(self) -> Iterable[str]: ...


class MemoryStore:
    """A store in memory, for tests and dry runs."""

    def __init__(self) -> None:
        self._lines: list[str] = []

    def append(self, line: str) -> None:
        self._lines.append(line)

    def lines(self) -> Iterable[str]:
        return list(self._lines)


class TrialConflict(ValueError):
    """A trial with this id is already recorded with different content."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


@dataclass(frozen=True)
class Trial:
    family: str
    hypothesis: str
    parameters: Mapping[str, Any]
    data_fingerprint: str
    code_version: str
    metrics: Mapping[str, Any]
    recorded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def content(self) -> dict[str, Any]:
        """What the id covers: everything except when it was recorded."""
        return {
            "family": self.family, "hypothesis": self.hypothesis, "parameters": dict(self.parameters),
            "data_fingerprint": self.data_fingerprint, "code_version": self.code_version,
            "metrics": dict(self.metrics),
        }

    @property
    def trial_id(self) -> str:
        return hashlib.sha256(_canonical(self.content()).encode("utf-8")).hexdigest()


def data_fingerprint(*parts: Any) -> str:
    """sha256 over the inputs a trial used (dataset ids, as-known-on times, row hashes)."""
    return hashlib.sha256(_canonical(list(parts)).encode("utf-8")).hexdigest()


class ExperimentLog:
    def __init__(self, store: Store | None = None) -> None:
        self._store = store or MemoryStore()

    def _records(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self._store.lines() if line.strip()]

    def record(self, trial: Trial) -> str:
        """Append ``trial`` unless it is already recorded; returns its id."""
        if not trial.family or not trial.hypothesis.strip():
            raise ValueError("a trial needs a family and a hypothesis in words")
        trial_id = trial.trial_id
        for existing in self._records():
            if existing["trial_id"] == trial_id:
                if _canonical(existing["content"]) != _canonical(trial.content()):
                    raise TrialConflict(trial_id)
                return trial_id
        self._store.append(_canonical({"trial_id": trial_id, "recorded_at": trial.recorded_at,
                                       "content": trial.content()}))
        return trial_id

    def trials(self, family: str | None = None) -> list[dict[str, Any]]:
        return [r for r in self._records() if family is None or r["content"]["family"] == family]

    def family_statistics(self, family: str, *, metric: str = "sharpe_ratio") -> dict[str, Any]:
        """Trial count and the variance of ``metric`` across the family's trials."""
        trials = self.trials(family)
        values = [float(t["content"]["metrics"][metric]) for t in trials
                  if t["content"]["metrics"].get(metric) is not None]
        return {
            "family": family,
            "trials": len(trials),
            "trials_with_metric": len(values),
            "metric": metric,
            "variance": statistics.variance(values) if len(values) > 1 else None,
            "best": max(values) if values else None,
        }
