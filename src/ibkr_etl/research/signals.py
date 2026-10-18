"""A small library of example signals for backtests: momentum, low volatility and mean reversion.

Each factory returns a signal for :func:`.backtester.run_backtest`: ``signal(view, day)`` reads
prices through the point-in-time view, so it only ever sees what was known at the decision
time, and returns long-only target weights that add up to at most 100%. They are starting
points for research, not recommendations, and nothing serves them.

* :func:`momentum` – hold the assets with the best return over a look-back window that ends
  ``skip`` sessions before the decision (the most recent days tend to reverse, so they are
  left out).
* :func:`low_volatility` – hold the assets whose daily returns were the steadiest.
* :func:`mean_reversion` – hold the assets that fell the most over a short window.

``top`` is how many assets to hold (default: the better half, at least one), equally
weighted at ``gross`` of NAV. An asset is ranked only once it has enough known prices; until
then the signal holds cash. Ties are broken by asset name, so results are reproducible.
Pure functions.
"""
from __future__ import annotations

import inspect
import math
import statistics
from typing import Any, Callable, Mapping, Sequence

from .backtester import Signal
from .point_in_time import PointInTimeView

MODEL_VERSION = "research-signals-v1"


def _check(name: str, value: int, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be a whole number of at least {minimum}")
    return value


def _settings(top: int | None, gross: float) -> tuple[int | None, float]:
    if top is not None:
        _check("top", top, 1)
    if not isinstance(gross, (int, float)) or isinstance(gross, bool) or not 0 < float(gross) <= 1:
        raise ValueError("gross must be above 0 and at most 1 (a fraction of NAV)")
    return top, float(gross)


def _closes(view: PointInTimeView, name: str, count: int) -> list[float] | None:
    """The last ``count`` known closes of ``name``, or None if fewer are known or any is unusable."""
    history = view.history(name, last=count)
    if len(history) < count:
        return None
    values = [float(value) for _, value in history]
    if any(not math.isfinite(value) or value <= 0 for value in values):
        return None
    return values


def _hold(scores: Mapping[str, float], *, top: int | None, gross: float, highest: bool) -> dict[str, float]:
    """Equal weights on the best ``top`` scores (highest or lowest first)."""
    if not scores:
        return {}
    count = top or max(1, len(scores) // 2)
    ranked = sorted(scores.items(), key=lambda item: (-item[1] if highest else item[1], item[0]))
    chosen = [name for name, _ in ranked[:count]]
    return {name: gross / len(chosen) for name in chosen}


def _universe(view: PointInTimeView, assets: Sequence[str] | None) -> list[str]:
    return sorted(assets) if assets is not None else sorted(view.names)


def momentum(*, lookback: int = 126, skip: int = 21, top: int | None = None, gross: float = 1.0,
             positive_only: bool = False, assets: Sequence[str] | None = None) -> Signal:
    """Hold the best performers from ``lookback + skip`` sessions ago to ``skip`` sessions ago."""
    _check("lookback", lookback, 1)
    _check("skip", skip, 0)
    top, gross = _settings(top, gross)

    def signal(view: PointInTimeView, day: str) -> dict[str, float]:
        scores = {}
        for name in _universe(view, assets):
            closes = _closes(view, name, lookback + skip + 1)
            if closes is None:
                continue
            change = closes[-1 - skip] / closes[0] - 1.0
            if positive_only and change <= 0:
                continue
            scores[name] = change
        return _hold(scores, top=top, gross=gross, highest=True)

    return signal


def low_volatility(*, lookback: int = 63, top: int | None = None, gross: float = 1.0,
                   assets: Sequence[str] | None = None) -> Signal:
    """Hold the assets with the lowest standard deviation of daily returns over ``lookback`` sessions."""
    _check("lookback", lookback, 2)
    top, gross = _settings(top, gross)

    def signal(view: PointInTimeView, day: str) -> dict[str, float]:
        scores = {}
        for name in _universe(view, assets):
            closes = _closes(view, name, lookback + 1)
            if closes is None:
                continue
            returns = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]
            scores[name] = statistics.stdev(returns)
        return _hold(scores, top=top, gross=gross, highest=False)

    return signal


def mean_reversion(*, lookback: int = 5, top: int | None = None, gross: float = 1.0,
                   assets: Sequence[str] | None = None) -> Signal:
    """Hold the assets that fell the most over the last ``lookback`` sessions."""
    _check("lookback", lookback, 1)
    top, gross = _settings(top, gross)

    def signal(view: PointInTimeView, day: str) -> dict[str, float]:
        scores = {}
        for name in _universe(view, assets):
            closes = _closes(view, name, lookback + 1)
            if closes is None:
                continue
            scores[name] = closes[-1] / closes[0] - 1.0
        return _hold(scores, top=top, gross=gross, highest=False)

    return signal


SIGNALS: dict[str, Callable[..., Signal]] = {
    "momentum": momentum,
    "low_volatility": low_volatility,
    "mean_reversion": mean_reversion,
}


def parameters(name: str) -> dict[str, Any]:
    """The parameters a signal takes and their defaults (``assets`` is set by the caller)."""
    factory = _factory(name)
    return {key: item.default for key, item in inspect.signature(factory).parameters.items() if key != "assets"}


def _factory(name: str) -> Callable[..., Signal]:
    try:
        return SIGNALS[name]
    except KeyError:
        raise ValueError(f"Unknown signal {name!r}; choose one of {', '.join(sorted(SIGNALS))}") from None


def build(name: str, settings: Mapping[str, Any] | None = None, *,
          assets: Sequence[str] | None = None) -> Signal:
    """The signal ``name`` with ``settings``; an unknown setting is refused rather than ignored."""
    settings = dict(settings or {})
    unknown = sorted(set(settings) - set(parameters(name)))
    if unknown:
        raise ValueError(f"Signal {name} has no setting {', '.join(unknown)}")
    return _factory(name)(**settings, assets=assets)
