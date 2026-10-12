"""Checks a drafted answer against the tool results it was built from.

Every figure in an answer must match a number some tool returned, allowing
for the rounding shown (12.3% matches 0.12345; $1.2m matches 1,234,567).
Every citation must name a tool call made for this answer. Answers that fail
are not shown to anyone.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

_NUMBER = re.compile(
    r"(?<![\w.])(?P<sign>[-+−])?(?P<cur>[$€£¥]|HK\$|US\$)?\s?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s?(?P<unit>%|bps|bp|k\b|m\b|bn\b|million\b|billion\b|thousand\b)?",
    re.IGNORECASE,
)
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_CITATION = re.compile(r"\[T\d+\]")
_SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "bn": 1e9, "billion": 1e9}
SMALL_COUNT_LIMIT = 10


@dataclass
class Validation:
    ok: bool
    unverified: list[str] = field(default_factory=list)
    bad_citations: list[str] = field(default_factory=list)
    bad_dates: list[str] = field(default_factory=list)

    def feedback(self) -> str:
        parts = []
        if self.unverified:
            parts.append("These figures do not match any tool result: " + ", ".join(self.unverified[:10]))
        if self.bad_citations:
            parts.append("These citations do not exist: " + ", ".join(self.bad_citations))
        if self.bad_dates:
            parts.append("These dates do not appear in any tool result: " + ", ".join(self.bad_dates[:10]))
        return ". ".join(parts)


def _numbers(value: Any) -> Iterable[float]:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        if math.isfinite(float(value)):
            yield float(value)
    elif isinstance(value, str):
        text = value.strip().replace(",", "")
        if re.fullmatch(r"[-+]?\d+(\.\d+)?([eE][-+]?\d+)?", text):
            yield float(text)
    elif isinstance(value, Mapping):
        for v in value.values():
            yield from _numbers(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _numbers(v)


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for k, v in value.items():
            yield str(k)
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


def _matches(shown: float, decimals: int, scale: float, candidates: list[float], sign: str | None = None) -> bool:
    """Match the shown magnitude; an explicit sign must also agree."""
    tolerance = 0.5 * 10 ** (-decimals) * scale + 1e-9
    if scale > 1:
        tolerance = max(tolerance, abs(shown) * 0.005)
    for c in candidates:
        if abs(abs(shown) - abs(c)) > tolerance:
            continue
        if sign in ("-", "−") and c > 0 and abs(c) > tolerance:
            continue
        if sign == "+" and c < 0 and abs(c) > tolerance:
            continue
        return True
    return False


def validate_answer(answer: str, citations: list[str], results: Mapping[str, Any], question: str = "") -> Validation:
    """``results`` maps citation id (T1, T2…) to the tool result it labels."""
    values = list(_numbers(list(results.values())))
    percents = [v * 100 for v in values] + values  # fractions and already-percent fields
    basis_points = [v * 10000 for v in values]
    known_strings = set(_strings(list(results.values())))
    question_numbers = {m.group("num").replace(",", "") for m in _NUMBER.finditer(question or "")}
    report = Validation(ok=True)

    report.bad_citations = sorted({c for c in citations if c not in results})
    inline = {m.group(0)[1:-1] for m in _CITATION.finditer(answer)}
    report.bad_citations += sorted(c for c in inline if c not in results and c not in report.bad_citations)

    for day in _DATE.findall(answer):
        if not any(day in s for s in known_strings):
            report.bad_dates.append(day)
    text = _CITATION.sub(" ", _DATE.sub(" ", answer))

    checked = 0
    for match in _NUMBER.finditer(text):
        raw = match.group("num").replace(",", "")
        unit = (match.group("unit") or "").lower()
        shown = float(raw)
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        if raw in question_numbers:
            continue
        if not unit and not match.group("cur") and decimals == 0:
            if shown <= SMALL_COUNT_LIMIT:
                continue  # counts and ordinals ("top 5", "3 holdings")
            if 1990 <= shown <= 2100 and any(s.startswith(raw) or f"-{raw}" in s or raw in s for s in known_strings):
                continue  # a year that appears in the data
        sign = match.group("sign")
        checked += 1
        if unit == "%":
            ok = _matches(shown, decimals, 1.0, percents, sign)
        elif unit in ("bp", "bps"):
            ok = _matches(shown, decimals, 1.0, basis_points, sign)
        elif unit in _SCALE:
            ok = _matches(shown * _SCALE[unit], decimals, _SCALE[unit], values, sign)
        else:
            ok = _matches(shown, decimals, 1.0, values, sign)
        if not ok:
            report.unverified.append(match.group(0).strip())

    if checked and not citations and not inline:
        report.bad_citations.append("(none given)")
    report.ok = not (report.unverified or report.bad_citations or report.bad_dates)
    return report
