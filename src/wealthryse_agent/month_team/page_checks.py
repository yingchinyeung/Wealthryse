"""Checks on a factsheet's printed text that need no model, and the guard on visual QA's findings.

Visual QA reads page images. In replay rounds 1 and 2 (checked against the released PDFs on 15 Oct 2026)
every one of its open factsheet objections was a false alarm: it misread digits in dates and figures (a day
read as another month, one value read as another), took a chart's point from the day before month-end as the
chart's end, and called a chart or table that ends on the month's last trading day the wrong period.
Meanwhile it raised none of the real faults: stale text dated after the report, the same sector printed
under two spellings, and template wording such as a reporting currency that is never stated.

So a visual finding that quotes a date or a figure the page does not print, estimates a value from a chart,
or objects to the last trading day is set aside (kept in the evidence with the reason, never silently
dropped), and the printed text is checked here directly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Sequence

_MONTHS = {name.lower(): number for number, names in enumerate(
    [("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",), ("jun", "june"),
     ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
     ("dec", "december")], start=1) for name in names}
_MONTH = "|".join(sorted(_MONTHS, key=len, reverse=True))
_ISO = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b")
_DAY_MONTH_YEAR = re.compile(rf"\b(\d{{1,2}})\s+({_MONTH})\.?,?\s+(20\d\d)\b", re.I)
_MONTH_DAY_YEAR = re.compile(rf"\b({_MONTH})\.?\s+(\d{{1,2}}),?\s+(20\d\d)\b", re.I)
# A decimal or a percentage as printed: 12.34, -5.67%, 1,234.5, 60%.
_FIGURE = re.compile(r"(?<![\w.])-?\d{1,3}(?:,\d{3})*(?:\.\d+)?%|(?<![\w.])-?\d[\d,]*\.\d+(?![\w.])")
_ESTIMATE = re.compile(r"\b(?:near|nearly|about|around|approximately|roughly|close to|just (?:above|below))\b[^.;:]{0,40}?\d",
                       re.I)
_ARITHMETIC = re.compile(r"\b(?:inconsisten\w*|does not (?:equal|match|add|reconcile)|doesn't (?:equal|match|add)|"
                         r"equals?|adds? up|sums? to|less|minus|should (?:be|read|equal)|recompute\w*|appears to show)\b",
                         re.I)
_ENDING = re.compile(r"\b(?:end|ends|ending|ended|through|until|last|final|latest|stops?|dated|labell?ed|period)\b", re.I)
_CURRENCY = re.compile(r"\b(?:USD|HKD|CNY|CNH|RMB|EUR|GBP|JPY|SGD|AUD)\b|US\$|HK\$|S\$|A\$|€|£|¥")
_SHORT_PERIOD = re.compile(r"\b(?:missing|does not (?:extend|reach|run|cover)|doesn't (?:extend|reach|run|cover)|"
                           r"stops? (?:short|before)|ends? (?:short|before)|incomplete)\b[^.]{0,80}?"
                           r"\b(?:months?|rows?|through|period|end(?:ing)? date)\b", re.I)
_YTD = re.compile(r"\bYTD\b|year[- ]to[- ]date", re.I)
_SPREAD = re.compile(r"\+\s*spread\b", re.I)
_AXIS_LABEL = re.compile(r"^(20\d\d)-(\d\d)$")
_BENCHMARK_NAMES = re.compile(r"S&P|Hang Seng|HSI\b|MSCI|HIBOR|SOFR|Nasdaq|NASDAQ|FTSE|CSI\s?300|Russell|Dow Jones|"
                              r"Nikkei|STOXX|Bloomberg", re.I)
_CALM = re.compile(r"\b(?:less|low|lower|reduced|minimal)\s+volatility\b", re.I)
# A history table row as printed: the ISO date, the NAV, then the month's change.
_MONTH_ROW = re.compile(r"\b(20\d\d-\d\d-\d\d)\s*\n\s*[\d,]+(?:\.\d+)?\s*\n\s*([+-]?\d+(?:\.\d+)?)%")
_LARGE_MONTH = 10.0  # percent; a month this large sits badly with "less volatility"
_VAR_METHOD = re.compile(r"historical|parametric|variance[- ]covariance|monte carlo|simulat|empirical|percentile|"
                         r"normal distribution", re.I)
_SHARPE_METHOD = re.compile(r"Sharpe[^.]{0,200}?(?:excess|minus|less|divided|over|annuali[sz]ed|√|sqrt)", re.I)
_CAP = re.compile(r"capped (?:below|at|under)\s+(\d+(?:\.\d+)?)\s*x\b", re.I)
_ROTATION = re.compile(r"\bdynamic allocation\b|\brotation process\b", re.I)
_MIXED_CASE = re.compile(r"\b[A-Za-z]*[a-z][A-Z][A-Za-z]*\b")  # WealthRyse, iStrategy
_FLOOR_TWICE = re.compile(r"\b(\d+)\s*/?\s*F\s*,\s*\1\s*/?\s*F\b")
_NEGATION = re.compile(r"\b(?:neither|not|no|never|none|nothing|cannot|without)\b|n't\b", re.I)
_DOUBLED = re.compile(r"\b([A-Za-z]{2,})[ \t]+\1\b", re.I)  # on one line: table cells repeat across lines

# The owner layout's allocation diagram (ibkr_etl.alpha_world_t_factsheet._allocation and the MS template) is
# fixed: two sleeves weighted 70% and 30%, the first 130% long and 70% short, the second 150% long and 150% short.
# On the T factsheet the diagram is drawn without a text layer, so the reviewer reads it from here.
OWNER_SLEEVES = ((70, 130, 70), (30, 150, 150))  # (weight, long, short), percent


def dates_in(text: str) -> list[date]:
    """Every full date written in the text (ISO, "31 Jan 2026", "January 31, 2026"), in order."""
    found: list[tuple[int, date]] = []
    for match in _ISO.finditer(text):
        found += _date(match.start(), int(match.group(1)), int(match.group(2)), int(match.group(3)))
    for match in _DAY_MONTH_YEAR.finditer(text):
        found += _date(match.start(), int(match.group(3)), _MONTHS[match.group(2).lower()], int(match.group(1)))
    for match in _MONTH_DAY_YEAR.finditer(text):
        found += _date(match.start(), int(match.group(3)), _MONTHS[match.group(1).lower()], int(match.group(2)))
    return [d for _, d in sorted(found)]


def _date(position: int, year: int, month: int, day: int) -> list[tuple[int, date]]:
    try:
        return [(position, date(year, month, day))]
    except ValueError:
        return []


def last_trading_day(period_end: date) -> date:
    """The last weekday on or before the month end. (A market holiday can make it earlier still.)"""
    day = period_end
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _closes_month(day: date, period_end: date) -> bool:
    """A date in the month's last few days on or before the month end: the last trading day, or one a
    holiday moved earlier (for example 29 May, or 30 Jun after a holiday)."""
    return period_end - timedelta(days=4) <= day <= period_end and day >= last_trading_day(period_end) - timedelta(days=2)


def _normal_figure(text: str) -> str:
    return text.replace(",", "").rstrip("%").lstrip("+")


def unconfirmed(description: str, pages: Sequence[str] | None, period_end: date | None) -> str | None:
    """Why a visual finding cannot stand against the printed text, or None if it may."""
    if _ESTIMATE.search(description):
        return "It estimates a value from the image; the charts are drawn from the same data as the tables."
    if pages is not None and _ARITHMETIC.search(description) and _FIGURE.search(description):
        return "It is arithmetic on printed figures, which the reviewer checks against the data in code."
    quoted = dates_in(description)
    if period_end is not None and quoted and _ENDING.search(description):
        others = [d for d in quoted if d != period_end]
        if others and all(_closes_month(d, period_end) for d in others):
            return (f"It ends on {others[0].isoformat()}, the month's last trading day, which is the correct end "
                    f"for a report dated {period_end.isoformat()}.")
    if pages is None:
        return None
    printed_text = "\n".join(pages)
    printed_dates = set(dates_in(printed_text))
    # Table rows are printed as ISO dates; headings print the report date in words, so only ISO dates show how far
    # a table really runs.
    row_dates: set[date] = set()
    for m in _ISO.finditer(printed_text):
        row_dates.update(d for _, d in _date(0, int(m.group(1)), int(m.group(2)), int(m.group(3))))
    if period_end is not None and _SHORT_PERIOD.search(description) and any(
            _closes_month(d, period_end) for d in row_dates):
        # Replay round 4: "the table does not extend through 31 May ... missing the final months", quoting no date,
        # on a table whose last row was 29 May, the last trading day.
        closing = max(d for d in row_dates if _closes_month(d, period_end))
        return (f"The printed text reaches {closing.isoformat()}, the month's last trading day, so nothing is missing "
                f"at the end of the period.")
    for day in quoted:
        if day not in printed_dates and day != period_end:
            return f"It quotes {day.isoformat()}, which the page does not print."
    printed_figures = {_normal_figure(m.group(0)) for m in _FIGURE.finditer(printed_text)}
    for match in _FIGURE.finditer(description):
        if _normal_figure(match.group(0)) not in printed_figures:
            return f"It quotes {match.group(0)}, which the page does not print."
    return None


def date_axes(text: str) -> list[list[str]]:
    """Each chart's month labels (YYYY-MM), as runs of four or more consecutive lines in the printed text."""
    axes: list[list[str]] = []
    run: list[str] = []
    for line in [*text.splitlines(), ""]:
        if _AXIS_LABEL.match(line.strip()):
            run.append(line.strip())
            continue
        if len(run) >= 4:
            axes.append(run)
        run = []
    return axes


def _months_between(a: str, b: str) -> int:
    return (int(b[:4]) - int(a[:4])) * 12 + int(b[5:7]) - int(a[5:7])


def _flat(s: str) -> str:
    s = s.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"-\s+", "-", s)  # a word the PDF hyphenates across a line break ("alpha-\ngenerated")
    return re.sub(r"\s+", " ", s).strip().lower()


def quoted_in(quote: str, pages: Sequence[str]) -> bool:
    """Whether a quotation appears in the printed text, ignoring line breaks, spacing, curly quotes, the quotation
    marks wrapped around it and a leading or trailing ellipsis. (Replay round 4: most real statements were set aside
    only because the model wrapped them in quotation marks.)"""
    core = _flat(quote).strip("\"' ").removeprefix("...").removesuffix("...").strip("… ")
    return len(core) >= 3 and core in _flat(" ".join(pages))


def figures_printed(text: str, pages: Sequence[str]) -> bool:
    """Whether every decimal or percentage quoted in ``text`` is printed on the pages."""
    printed = {_normal_figure(m.group(0)) for m in _FIGURE.finditer("\n".join(pages))}
    return all(_normal_figure(m.group(0)) in printed for m in _FIGURE.finditer(text))


@dataclass
class TextFindings:
    objections: list[dict[str, Any]] = field(default_factory=list)  # month-specific faults
    notes: list[str] = field(default_factory=list)  # template wording to decide once, not every month


def _sentences(text: str, pattern: re.Pattern[str]) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", " ".join(text.split())) if pattern.search(s)]


def owner_gross_exposure() -> int:
    """Gross exposure of the owner layout's fixed sleeves, in percent of capital."""
    return round(sum(weight * (long + short) for weight, long, short in OWNER_SLEEVES) / 100)


def wording_notes(pages: Sequence[str], *, owner_layout: bool = False) -> list[str]:
    """Template wording the printed figures or the layout contradict, and proofreading slips. (Replay round 5 and the
    independent check of 15 Oct 2026: "Less Volatility" beside months of 10% or more, VaR and Sharpe methods never
    stated, "capped below 2x" on a layout that is 2.3x gross, the brand printed two ways, and typos in the
    disclosures.)"""
    notes: list[str] = []
    everything = "\n".join(pages)
    if not everything.strip():
        return notes
    calm = _CALM.search(everything)
    if calm:
        months = [(abs(float(m.group(2))), m.group(2), m.group(1)) for m in _MONTH_ROW.finditer(everything)]
        large = [m for m in months if m[0] >= _LARGE_MONTH]
        if large:
            _, change, day = max(large)
            notes.append(f'The text says "{" ".join(calm.group(0).split())}", but the history table prints '
                         f"{len(large)} month(s) of {_LARGE_MONTH:.0f}% or more, up to {change}% (to {day}).")
    var = _sentences(everything, re.compile(r"\bC?VaR\b"))
    if var and not any(_VAR_METHOD.search(s) for s in var):
        notes.append("VaR and CVaR are given a confidence level but not a method (for example historical: the 5th "
                     "percentile of daily returns over the period shown).")
    if (re.search(r"\bSharpe\b", everything) or owner_layout or re.search(r"risk[- ]free rate", everything, re.I)) \
            and not _SHARPE_METHOD.search(" ".join(everything.split())):
        notes.append("A Sharpe ratio is shown without its method (for example annualised mean daily return less the "
                     "risk-free rate, divided by annualised volatility).")
    cap = _CAP.search(everything)
    if owner_layout and cap and owner_gross_exposure() >= float(cap.group(1)) * 100:
        parts = " + ".join(f"{w}% × ({lg}% + {sh}%)" for w, lg, sh in OWNER_SLEEVES)
        notes.append(f'The text says leverage is "{" ".join(cap.group(0).split())}", but the allocation diagram\'s '
                     f"fixed sleeves are {owner_gross_exposure()}% gross ({parts}).")
    if owner_layout and _ROTATION.search(everything):
        notes.append("The text describes a dynamic allocation and rotation between sleeves, but the allocation "
                     "diagram is the same fixed split every month.")
    forms: dict[str, set[str]] = {}
    for word in _MIXED_CASE.findall(everything):
        forms.setdefault(word.lower(), set())
    for word in re.findall(r"\b[A-Za-z]+\b", everything):
        if word.lower() in forms and not word.isupper():
            forms[word.lower()].add(word)
    for spelled in forms.values():
        if len(spelled) > 1:
            notes.append(f"A name is printed two ways: {' and '.join(sorted(spelled))}.")
    slips = [m.group(0) for m in _FLOOR_TWICE.finditer(everything)]
    slips += [m.group(0) for m in _DOUBLED.finditer(everything) if m.group(1).lower() not in {"that", "had"}]
    for sentence in _sentences(everything, re.compile(r"\bnor\b")):
        before = sentence[:sentence.find(" nor ")] if " nor " in sentence else ""
        if before and not _NEGATION.search(before[-80:]):
            slips.append(" ".join(before.split()[-4:]) + " nor")
    for slip in dict.fromkeys(slips):
        notes.append(f'Proofreading: "{slip}".')
    return notes


def check_text(pages: Sequence[str], period_end: date, *, owner_layout: bool = False) -> TextFindings:
    """Faults that the printed text proves, without a model."""
    result = TextFindings()
    for number, text in enumerate(pages, start=1):
        later = sorted({d for d in dates_in(text) if d > period_end})
        if later:
            result.objections.append({"page": number, "message": (
                f"Stale date: the page's text includes {later[-1].isoformat()}, after the report date "
                f"{period_end.isoformat()}. It may be left over from another month, possibly hidden under a box, "
                "where copy, search and screen readers still find it.")})
        spellings: dict[str, set[str]] = {}
        for line in text.splitlines():
            label = line.strip()
            key = re.sub(r"[^a-z]", "", label.lower())
            if 6 <= len(key) <= 60 and not any(ch.isdigit() for ch in label):
                spellings.setdefault(key, set()).add(label)
        for labels in spellings.values():
            if len(labels) > 1:
                shown = " and ".join(f'"{x}"' for x in sorted(labels))
                result.objections.append({"page": number, "message": (
                    f"The same label is printed two ways, {shown}, so one category may be split across two rows.")})
    for number, text in enumerate(pages, start=1):
        for labels in date_axes(text):
            gaps = [_months_between(a, b) for a, b in zip(labels, labels[1:])]
            if len(set(gaps)) > 1:
                result.objections.append({"page": number, "message": (
                    f"A chart's date labels are unevenly spaced ({', '.join(labels)}: gaps of "
                    f"{', '.join(str(g) for g in gaps)} months), so some may crowd or overlap.")})
    everything = "\n".join(pages)
    if everything.strip() and not _YTD.search(everything):
        result.notes.append("No year-to-date return is printed.")
    if _SPREAD.search(everything) and not re.search(r"spread\W{0,3}(?:of\s+)?[+-]?\d", everything, re.I):
        result.notes.append("The benchmark adds a spread that is never stated.")
    if everything.strip() and not _CURRENCY.search(everything):
        result.notes.append("The reporting currency is not stated anywhere on the factsheet.")
    if re.search(r"\bbenchmark\b", everything, re.I) and not _BENCHMARK_NAMES.search(everything):
        result.notes.append("The factsheet refers to a benchmark but never names one or shows its return.")
    twelve = re.search(r"12[- ]?month return[^.]{0,200}?weighted average", everything, re.I | re.S)
    if twelve:
        result.notes.append("The 12-month return note describes a weighted average of holdings; check it matches how the "
                            "printed figure is calculated (it may be the NAV-to-NAV change).")
    result.notes += wording_notes(pages, owner_layout=owner_layout)
    return result
