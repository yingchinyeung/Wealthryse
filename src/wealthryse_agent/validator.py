"""Checks a drafted answer against the tool results it was built from.

Every figure in an answer must match a number some tool returned, allowing
for the rounding shown (12.3% matches 0.12345; $1.2m matches 1,234,567).
Every citation must name a tool call made for this answer. Answers that fail
are not shown to anyone.

Certified figures (get_certified_figures, behind AGENT_CERTIFIED_FIGURES) are
cited [F1], [F2]: the number beside an [F#] must be that figure's value, not
just any number a tool returned. By default (AGENT_CERTIFIED_FIGURE_CHECK unset
or warn) that check only warns (Validation.figure_warnings); it never rejects
an answer. Without certified figures the check does not run and nothing changes.

With figure_check="enforce" (AGENT_CERTIFIED_FIGURE_CHECK=enforce) two more
rules fail the answer, as an unmatched number does: the number beside each
[F#] must be that figure's value (and the ref must exist), and a number whose
only source is a certified figure (get_certified_figures, or a figure tool's
``certified`` block) must be cited by its [F#], not quoted from the tool call.
A number some other tool result also returned is allowed either way. The
runtime then asks once for a corrected answer and, if that fails too, shows
none (the existing reject path).
"""
from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

# Korean units (만 10^4, 억 10^8, 조 10^12) carry no word boundary, since 원 or a particle can follow them.
_NUMBER = re.compile(
    r"(?<![\w.])(?P<sign>[-+−])?(?P<cur>[$€£¥₩]|HK\$|US\$)?\s?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s?(?P<unit>%|bps|bp|k\b|m\b|bn\b|million\b|billion\b|thousand\b|만|억|조)?",
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
_SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "bn": 1e9, "billion": 1e9,
          "만": 1e4, "억": 1e8, "조": 1e12}
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
    # Enforce mode only: certified figures quoted without their [F#] (with the number, for the
    # model), and their refs (for logs).
    quoted_elsewhere: list[str] = field(default_factory=list)
    quoted_refs: list[str] = field(default_factory=list)
    figure_mode: str = "warn"

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
        if self.figure_mode == "enforce" and self.figure_warnings:
            parts.append("Certified figures: " + "; ".join(self.figure_warnings[:10]))
        if self.figure_mode == "enforce" and self.quoted_elsewhere:
            parts.append("These figures are certified, so cite each by its ref instead: "
                         + ", ".join(self.quoted_elsewhere[:10]))
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


def _cited_spans(answer: str, figures: Mapping[str, Mapping[str, Any]]) -> list[tuple[int, int]]:
    """Where in ``answer`` the number each [F#] citation is checked against sits (as
    check_figure_citations picks it: the last number in the clause before the citation)."""
    spans: list[tuple[int, int]] = []
    start = 0
    for citation in _ANY_CITATION.finditer(answer):
        offset, span = start, answer[start:citation.start()]
        start = citation.end()
        ref = citation.group(0)[1:-1]
        if not ref.startswith("F") or ref not in figures:
            continue
        ends = list(_CLAUSE_END.finditer(span))
        if ends:
            offset, span = offset + ends[-1].end(), span[ends[-1].end():]
        numbers = list(_NUMBER.finditer(_DATE.sub(lambda m: " " * len(m.group(0)), span)))
        if numbers:
            spans.append((offset + numbers[-1].start(), offset + numbers[-1].end()))
    return spans


def _pointer_clear(result: Any, pointer: str) -> None:
    """Blank the value a JSON pointer (/data/periods/2/twr) names, if it is there."""
    parts = [part.replace("~1", "/").replace("~0", "~") for part in str(pointer).split("/")[1:]]
    node = result
    for part in parts[:-1]:
        if isinstance(node, Mapping):
            node = node.get(part)
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return
    if parts and isinstance(node, dict) and parts[-1] in node:
        node[parts[-1]] = None
    elif parts and isinstance(node, list) and parts[-1].isdigit() and int(parts[-1]) < len(node):
        node[int(parts[-1])] = None


def uncertified_results(results: Mapping[str, Any], figures: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """The tool results without their certified figures: no get_certified_figures figures, no
    ``certified`` blocks, and no value a certified figure was used for (its ``at`` pointer)."""
    stripped = copy.deepcopy(dict(results))
    for result in stripped.values():
        if isinstance(result, dict):
            result.pop("certified", None)
            if result.get("tool") == "get_certified_figures":
                result.pop("figures", None)
    for figure in figures.values():
        call, at = figure.get("tool_call"), figure.get("at")
        if call in stripped and at:
            _pointer_clear(stripped[call], at)
    return stripped


def _scrubbed(text: str, known_strings: set[str]) -> str:
    """As validate_answer prepares the text before matching numbers."""
    text = _CITATION.sub(" ", _DATE.sub(" ", _FIGURE_CITATION.sub(" ", text)))
    for name in sorted((x for x in known_strings if _NAMED_NUMBER.search(x) and len(x) <= 80), key=len, reverse=True):
        if name in text:
            text = re.sub(r"(?<!\w)" + re.escape(name) + r"(?!\w)", " ", text)
    for clock in _TIME.findall(text):
        if any(clock.zfill(5) in x or clock in x for x in known_strings):
            text = re.sub(r"(?<![\w:])" + re.escape(clock) + r"(?![\w:])", " ", text)
    return text


def _in_pool(match: re.Match, known: Mapping[str, list[float]]) -> bool:
    raw = match.group("num").replace(",", "")
    unit = (match.group("unit") or "").lower()
    shown, decimals, sign = float(raw), (len(raw.split(".")[1]) if "." in raw else 0), match.group("sign")
    if unit == "%":
        return _matches(shown, decimals, 1.0, known["percents"], sign)
    if unit in ("bp", "bps"):
        return _matches(shown, decimals, 1.0, known["basis_points"], sign)
    if unit in _SCALE:
        return _matches(shown * _SCALE[unit], decimals, _SCALE[unit], known["values"], sign)
    return _matches(shown, decimals, 1.0, known["values"], sign)


def quoted_elsewhere(answer: str, results: Mapping[str, Any], figures: Mapping[str, Mapping[str, Any]],
                     question: str = "") -> tuple[list[str], list[str]]:
    """Numbers whose only source is a certified figure but that are not the number beside its
    [F#]: the shown numbers (for the model) and the refs (for logs)."""
    masked = list(answer)
    for begin, end in _cited_spans(answer, figures):
        masked[begin:end] = " " * (end - begin)
    known_strings = set(_strings(list(results.values())))
    text = _scrubbed("".join(masked), known_strings)
    question_numbers = {m.group("num").replace(",", "") for m in _NUMBER.finditer(question or "")}
    numeric = {ref: f for ref, f in figures.items()
               if str(f.get("unit") or "") not in ("date", "text") and isinstance(f.get("value"), (int, float))
               and not isinstance(f.get("value"), bool)}
    pool = known_numbers(uncertified_results(results, figures))
    shown, refs = [], []
    for match in _NUMBER.finditer(text):
        raw = match.group("num").replace(",", "")
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        if raw in question_numbers:
            continue
        if not match.group("unit") and not match.group("cur") and decimals == 0 and (
                float(raw) <= SMALL_COUNT_LIMIT or 1990 <= float(raw) <= 2100):
            continue  # counts, ordinals and years, as validate_answer skips them
        hits =[ref for ref, f in numeric.items() if _shown_matches(match, float(f["value"]), str(f.get("unit") or ""))]
        if hits and not _in_pool(match, pool):
            shown.append(f"{match.group(0).strip()} is {'/'.join(f'[{ref}]' for ref in hits)}")
            refs += [ref for ref in hits if ref not in refs]
    return shown, refs


def validate_answer(answer: str, citations: list[str], results: Mapping[str, Any], question: str = "",
                    visuals: list[Mapping[str, Any]] | None = None,
                    figures: Mapping[str, Mapping[str, Any]] | None = None,
                    figure_check: str = "warn") -> Validation:
    """``results`` maps citation id (T1, T2…) to the tool result it labels; ``figures`` maps F1, F2…
    to the certified figures those results carried (None when there are none). ``figure_check``
    is warn (the [F#] check only warns) or enforce (it fails the answer)."""
    original_answer = answer
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
    if figure_check == "enforce" and figures is not None:
        report.figure_mode = "enforce"
        report.quoted_elsewhere, report.quoted_refs = quoted_elsewhere(original_answer, results, figures, question)
        report.ok = report.ok and not (report.figure_warnings or report.quoted_elsewhere)
    return report
