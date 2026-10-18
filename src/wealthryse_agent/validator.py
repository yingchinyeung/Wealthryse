"""Checks a drafted answer against the tool results it was built from.

Every figure in an answer must match a number some tool returned, allowing
for the rounding shown (12.3% matches 0.12345; $1.2m matches 1,234,567).
Every citation must name a tool call made for this answer. Answers that fail
are not shown to anyone.

Certified figures (get_certified_figures, behind AGENT_CERTIFIED_FIGURES) are
cited [F1], [F2]: the number beside an [F#] must be that figure's value, not
just any number a tool returned. Until 8 Nov that check only warns
(Validation.figure_warnings); it never rejects an answer. Without certified
figures the check does not run and nothing changes.
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
_TIME = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
_CITATION = re.compile(r"\[T\d+\]")
_FIGURE_CITATION = re.compile(r"\[F\d+\]")
_FIGURE_ID = re.compile(r"F\d+")
_ANY_CITATION = re.compile(r"\[[TF]\d+\]")
_CLAUSE_END = re.compile(r"[.!?;](?=\s)|\n")
_NAMED_NUMBER = re.compile(r"[A-Za-z].*\d|\d.*[A-Za-z]")
_SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "bn": 1e9, "billion": 1e9}
SMALL_COUNT_LIMIT = 10


@dataclass
class Validation:
    ok: bool
    unverified: list[str] = field(default_factory=list)
    bad_citations: list[str] = field(default_factory=list)
    bad_dates: list[str] = field(default_factory=list)
    bad_visuals: list[str] = field(default_factory=list)
    # Warn-only until 8 Nov: [F#] citations whose number is not that certified figure's value.
    figure_warnings: list[str] = field(default_factory=list)
    figure_checks: dict[str, str] = field(default_factory=dict)  # F# -> "ok" or "warning"

    def feedback(self) -> str:
        parts = []
        if self.unverified:
            parts.append("These figures do not match any tool result: " + ", ".join(self.unverified[:10]))
        if self.bad_citations:
            parts.append("These citations do not exist: " + ", ".join(self.bad_citations))
        if self.bad_dates:
            parts.append("These dates do not appear in any tool result: " + ", ".join(self.bad_dates[:10]))
        if self.bad_visuals:
            parts.append("Charts and tables: " + "; ".join(self.bad_visuals[:10]))
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


def known_numbers(results: Mapping[str, Any]) -> dict[str, list[float]]:
    values = list(_numbers(list(results.values())))
    return {"values": values, "percents": [v * 100 for v in values] + values,  # fractions and already-percent fields
            "basis_points": [v * 10000 for v in values]}


def _decimals(value: float) -> int:
    text = repr(float(value))
    if "e" in text or "E" in text:
        return 12
    return len(text.split(".")[1].rstrip("0")) if "." in text else 0


def number_is_known(value: float, known: Mapping[str, list[float]], signed: bool = False) -> bool:
    """A bare number (in a calculation or chart) must be a tool figure, possibly rounded or as a percent.

    Calculations check magnitude only (a minus sign there is an operator); chart values check the sign too.
    """
    decimals = _decimals(value)
    sign = ("-" if value < 0 else "+" if value > 0 else None) if signed else None
    return _matches(value, decimals, 1.0, known["values"], sign) or _matches(value, decimals, 1.0, known["percents"], sign)


MAX_VISUALS = 4
MAX_POINTS = 400
MAX_SERIES = 8


def validate_visuals(visuals: list[Mapping[str, Any]], results: Mapping[str, Any],
                     known: Mapping[str, list[float]], known_strings: set[str]) -> list[str]:
    """Every value in a chart or table must be a tool figure; every source a real citation."""
    problems: list[str] = []
    if len(visuals) > MAX_VISUALS:
        problems.append(f"at most {MAX_VISUALS} charts or tables per answer")
    for n, visual in enumerate(visuals[:MAX_VISUALS], start=1):
        name = f"visual {n}"
        labels = [str(x) for x in visual.get("labels") or []]
        series = list(visual.get("series") or [])
        sources = [str(x) for x in visual.get("sources") or []]
        if not labels or len(labels) > MAX_POINTS:
            problems.append(f"{name} needs 1 to {MAX_POINTS} labels")
        if not series or len(series) > MAX_SERIES:
            problems.append(f"{name} needs 1 to {MAX_SERIES} series")
        if visual.get("kind") == "pie" and len(series) != 1:
            problems.append(f"{name} is a pie chart and must have exactly one series")
        missing = [c for c in sources if c not in results]
        if not sources or missing:
            problems.append(f"{name} must cite the tool results it shows" + (f" ({', '.join(missing)} do not exist)" if missing else ""))
        bad_dates = [d for label in labels for d in _DATE.findall(label) if not any(d in s for s in known_strings)]
        if bad_dates:
            problems.append(f"{name} has dates no tool returned: {', '.join(bad_dates[:5])}")
        unverified: list[str] = []
        for s in series:
            # Percent series hold fractions (0.05 is drawn as 5%); a tool field already in percent counts /100.
            pool = ({"values": known["values"] + [v / 100 for v in known["values"]], "percents": []}
                    if s.get("unit") == "percent" else known)
            values = list(s.get("values") or [])
            if len(values) != len(labels):
                problems.append(f"{name} series '{s.get('name')}' must have one value per label")
                continue
            for v in values:
                if v is None:
                    continue
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                    unverified.append(str(v))
                elif visual.get("kind") == "pie" and v < 0:
                    problems.append(f"{name} is a pie chart and cannot show negative values")
                elif not number_is_known(float(v), pool, signed=True):
                    unverified.append(f"{v:g}")
        if unverified:
            problems.append(f"{name} has values no tool returned: {', '.join(unverified[:10])}")
    return problems


def _shown_matches(match: re.Match, value: float, unit: str) -> bool:
    raw = match.group("num").replace(",", "")
    decimals = len(raw.split(".")[1]) if "." in raw else 0
    shown, symbol, sign = float(raw), (match.group("unit") or "").lower(), match.group("sign")
    if symbol == "%":
        candidates = [value * 100] if unit == "ratio" else [value, value * 100]
        return _matches(shown, decimals, 1.0, candidates, sign)
    if symbol in ("bp", "bps"):
        return _matches(shown, decimals, 1.0, [value * 10000], sign)
    if symbol in _SCALE:
        return _matches(shown * _SCALE[symbol], decimals, _SCALE[symbol], [value], sign)
    return _matches(shown, decimals, 1.0, [value], sign)


def check_figure_citations(answer: str, figures: Mapping[str, Mapping[str, Any]]) -> tuple[list[str], dict[str, str]]:
    """The number beside each [F#] (the last one between the previous citation or clause end and
    the citation) must be that certified figure's value, allowing for the rounding shown.
    Returns the warnings and each cited figure's result."""
    warnings: list[str] = []
    checks: dict[str, str] = {}
    start = 0
    for citation in _ANY_CITATION.finditer(answer):
        span = answer[start:citation.start()]
        start = citation.end()
        ref = citation.group(0)[1:-1]
        if not ref.startswith("F"):
            continue
        figure = figures.get(ref)
        if figure is None:
            warnings.append(f"{ref} is not a certified figure returned for this answer")
            continue
        ends = list(_CLAUSE_END.finditer(span))
        clause = span[ends[-1].end():] if ends else span
        unit, value = str(figure.get("unit") or ""), figure.get("value")
        if unit in ("date", "text"):
            ok = value is not None and str(value) in clause
        else:
            numbers = list(_NUMBER.finditer(_DATE.sub(" ", clause)))
            ok = bool(numbers) and isinstance(value, (int, float)) and _shown_matches(numbers[-1], float(value), unit)
        checks[ref] = checks.get(ref) if checks.get(ref) == "warning" else ("ok" if ok else "warning")
        if not ok:
            warnings.append(f"{ref} ({figure.get('figure_id')}) is {figure.get('display')}, but the text beside it "
                            "does not show that value")
    return warnings, checks


def validate_answer(answer: str, citations: list[str], results: Mapping[str, Any], question: str = "",
                    visuals: list[Mapping[str, Any]] | None = None,
                    figures: Mapping[str, Mapping[str, Any]] | None = None) -> Validation:
    """``results`` maps citation id (T1, T2…) to the tool result it labels; ``figures`` maps F1, F2…
    to the certified figures those results carried (None when there are none)."""
    known = known_numbers(results)
    values, percents, basis_points = known["values"], known["percents"], known["basis_points"]
    known_strings = set(_strings(list(results.values())))
    question_numbers = {m.group("num").replace(",", "") for m in _NUMBER.finditer(question or "")}
    report = Validation(ok=True)

    figure_refs = [c for c in citations if figures is not None and _FIGURE_ID.fullmatch(c)]
    citations = [c for c in citations if c not in figure_refs]
    cited_figure = False
    if figures is not None:
        report.figure_warnings, report.figure_checks = check_figure_citations(answer, figures)
        report.figure_warnings += [f"{c} is not a certified figure returned for this answer"
                                   for c in figure_refs if c not in figures]
        cited_figure = any(c in figures for c in figure_refs) or any(
            m.group(0)[1:-1] in figures for m in _FIGURE_CITATION.finditer(answer))
        answer = _FIGURE_CITATION.sub(" ", answer)

    report.bad_citations = sorted({c for c in citations if c not in results})
    inline = {m.group(0)[1:-1] for m in _CITATION.finditer(answer)}
    report.bad_citations += sorted(c for c in inline if c not in results and c not in report.bad_citations)

    for day in _DATE.findall(answer):
        if not any(day in s for s in known_strings):
            report.bad_dates.append(day)
    text = _CITATION.sub(" ", _DATE.sub(" ", answer))
    # Names that contain digits ("S&P 500", "FTSE 100") are quoted text, not figures.
    for name in sorted((x for x in known_strings if _NAMED_NUMBER.search(x) and len(x) <= 80), key=len, reverse=True):
        if name in text:
            text = re.sub(r"(?<!\w)" + re.escape(name) + r"(?!\w)", " ", text)
    # A clock time is fine when a tool returned it (for example in an import timestamp).
    for clock in _TIME.findall(text):
        if any(clock.zfill(5) in x or clock in x for x in known_strings):
            text = re.sub(r"(?<![\w:])" + re.escape(clock) + r"(?![\w:])", " ", text)

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

    if checked and not citations and not inline and not cited_figure:
        report.bad_citations.append("(none given)")
    report.bad_visuals = validate_visuals(list(visuals or []), results, known, known_strings)
    report.ok = not (report.unverified or report.bad_citations or report.bad_dates or report.bad_visuals)
    return report
