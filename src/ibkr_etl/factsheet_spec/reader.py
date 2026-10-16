"""The template reader: a deterministic inventory of what an uploaded factsheet shows, and where.

The reader never reads a number from an image. It replays each page's content
stream (``paint.scan_page``) and returns:

- ``spans``: text joined into words and phrases, with page-space boxes, font, size
  and colour. Templates made in Word or PowerPoint place every glyph on its own,
  so glyph runs are joined by their real advances from the font's widths;
- ``fills``: filled rectangles (panels, table bands, chart backgrounds);
- ``images``: placed images, by box;
- ``graphics``: dense areas of stroked paths, which are almost always charts.

``variable_fields`` then decides which spans are values a renderer must fill
each month. With a completed example, they are the spans that differ between
the template and the example; without one, they are placeholders (``XX.XX%``,
``[Name]``, ``DD/MM/YYYY``), figures and dates left in the template. Each field
comes with candidate bindings from the catalogue, chosen by the label printed
next to it and, when a snapshot for the example's month is supplied, by matching
the printed value against that snapshot's own figures.

Everything here is code the owner can audit; the builder agent starts from this
inventory and never sees an image.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from pypdf import PdfReader

from . import bindings
from .paint import TextRun, scan_page

JOIN_GAP_EM = 0.55        # glyphs closer than this (in ems) belong to one phrase
SPACE_GAP_EM = 0.12       # a gap wider than this between glyphs is a space
BASELINE_EM = 0.2         # glyphs whose baselines differ less than this share a line
GRAPHIC_MIN_PATHS = 12    # stroked paths in one area before it is called a chart
MAX_SPANS_PER_PAGE = 4000


@dataclass
class Span:
    page: int
    text: str
    x: float
    y: float
    width: float
    size: float
    font: str
    color: str
    angle: float = 0.0

    @property
    def box(self) -> list[float]:
        """[x, y, width, height] from the descender to the ascender."""
        return [round(self.x, 2), round(self.y - 0.22 * self.size, 2), round(self.width, 2),
                round(0.95 * self.size, 2)]

    @property
    def right(self) -> float:
        return self.x + self.width

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "box": self.box}


@dataclass
class PageInventory:
    number: int
    size: tuple[float, float]
    spans: list[Span] = field(default_factory=list)
    fills: list[dict[str, Any]] = field(default_factory=list)
    images: list[dict[str, Any]] = field(default_factory=list)
    graphics: list[dict[str, Any]] = field(default_factory=list)
    strokes: list[list[float]] = field(default_factory=list)  # stroked rectangles (table grids), as boxes


@dataclass
class Inventory:
    sha256: str
    pages: list[PageInventory]

    def spans(self) -> list[Span]:
        return [span for page in self.pages for span in page.spans]

    def summary(self) -> dict[str, Any]:
        """A compact, JSON-safe description for the builder agent and the review page."""
        return {"sha256": self.sha256, "pages": [{
            "page": p.number + 1, "size": list(p.size),
            "spans": [s.as_dict() for s in p.spans],
            "fills": p.fills, "images": p.images, "graphics": p.graphics,
        } for p in self.pages]}


def _hex_color(fill: tuple[str, tuple[float, ...]]) -> str:
    space, values = fill
    try:
        if space == "g" and len(values) == 1:
            r = g = b = values[0]
        elif space in ("rg", "sc", "scn") and len(values) == 3:
            r, g, b = values
        elif space == "k" and len(values) == 4:
            c, m, y, k = values
            r, g, b = (1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k)
        else:
            return "#000000"
        return "#" + "".join(f"{max(0, min(255, round(float(v) * 255))):02x}" for v in (r, g, b))
    except (TypeError, ValueError):
        return "#000000"


def join_runs(runs: Iterable[TextRun], page: int) -> list[Span]:
    """Join glyph runs into phrases: same line, font, size and colour, and close enough to read as one."""
    ordered = sorted((r for r in runs if r.text), key=lambda r: (r.angle, -round(r.y / max(r.size, 1) * 5), r.x))
    spans: list[Span] = []
    for run in ordered:
        color = _hex_color(run.fill)
        font = run.font or ""
        width = run.width or 0.5 * run.size * len(run.text)
        last = spans[-1] if spans else None
        if (last is not None and last.angle == run.angle and last.font == font and last.color == color
                and abs(last.size - run.size) <= 0.05 * run.size
                and abs(last.y - run.y) <= BASELINE_EM * run.size
                and -0.2 * run.size <= run.x - last.right <= JOIN_GAP_EM * run.size):
            gap = run.x - last.right
            last.text += (" " if gap > SPACE_GAP_EM * run.size and not last.text.endswith(" ") else "") + run.text
            last.width = max(last.width, run.x + width - last.x)
            continue
        spans.append(Span(page, run.text, run.x, run.y, width, run.size, font, color, run.angle))
    for span in spans:
        span.text = re.sub(r"\s+", " ", span.text).strip()
        span.x, span.y, span.width, span.size = (round(span.x, 2), round(span.y, 2), round(span.width, 2),
                                                 round(span.size, 2))
    return [s for s in spans if s.text]


def _bounds(geometry: Sequence[Sequence[Any]]) -> tuple[float, float, float, float] | None:
    xs, ys = [], []
    for segment in geometry:
        numbers = [v for v in segment[1:] if isinstance(v, (int, float))]
        xs.extend(numbers[0::2])
        ys.extend(numbers[1::2])
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _graphics(paths: list[tuple[float, float, float, float]], size: tuple[float, float]) -> list[dict[str, Any]]:
    """Cluster small stroked paths into chart-like areas (grid lines, series, axes)."""
    cells: dict[tuple[int, int], list[tuple[float, float, float, float]]] = {}
    step = 40.0
    for b in paths:
        cells.setdefault((int(b[0] // step), int(b[1] // step)), []).append(b)
    seen: set[tuple[int, int]] = set()
    areas = []
    for start in cells:
        if start in seen:
            continue
        group, queue = [], [start]
        seen.add(start)
        while queue:
            cell = queue.pop()
            group.extend(cells[cell])
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    neighbour = (cell[0] + dx, cell[1] + dy)
                    if neighbour in cells and neighbour not in seen:
                        seen.add(neighbour)
                        queue.append(neighbour)
        if len(group) < GRAPHIC_MIN_PATHS:
            continue
        x0, y0 = min(b[0] for b in group), min(b[1] for b in group)
        x1, y1 = max(b[2] for b in group), max(b[3] for b in group)
        if (x1 - x0) < 40 or (y1 - y0) < 30 or (x1 - x0) > 0.98 * size[0]:
            continue
        areas.append({"box": [round(x0, 2), round(y0, 2), round(x1 - x0, 2), round(y1 - y0, 2)],
                      "paths": len(group)})
    return areas


def inventory(path: str | Path) -> Inventory:
    data = Path(path).read_bytes()
    reader = PdfReader(str(path))
    pages = []
    for number, page in enumerate(reader.pages):
        size = (float(page.mediabox.width), float(page.mediabox.height))
        scan = scan_page(page)
        item = PageInventory(number, size, join_runs(scan.runs, number)[:MAX_SPANS_PER_PAGE])
        strokes = []
        for op in scan.ops:
            if op[0] == "path":
                bounds = _bounds(op[2])
                if bounds is None:
                    continue
                x0, y0, x1, y1 = bounds
                if op[3] is not None and op[2] and op[2][0][0] == "re" and (x1 - x0) > 2 and (y1 - y0) > 2:
                    color = _hex_color(op[3])
                    if color != "#ffffff" and (x1 - x0) * (y1 - y0) < 0.9 * size[0] * size[1]:
                        item.fills.append({"box": [round(x0, 2), round(y0, 2), round(x1 - x0, 2),
                                                   round(y1 - y0, 2)], "color": color})
                if op[4] is not None or op[3] is not None:
                    strokes.append(bounds)
                if op[4] is not None and op[2] and op[2][0][0] == "re" and len(item.strokes) < 4000:
                    item.strokes.append([round(x0, 2), round(y0, 2), round(x1 - x0, 2), round(y1 - y0, 2)])
            elif op[0] == "image":
                m = op[2]
                x0, x1 = sorted((m[4], m[4] + m[0] + m[2]))
                y0, y1 = sorted((m[5], m[5] + m[1] + m[3]))
                item.images.append({"box": [round(x0, 2), round(y0, 2), round(x1 - x0, 2), round(y1 - y0, 2)],
                                    "digest": op[1]})
        item.graphics = _graphics([b for b in strokes if (b[2] - b[0]) < 0.9 * size[0]], size)
        pages.append(item)
    return Inventory(hashlib.sha256(data).hexdigest(), pages)


# ---- variable fields -------------------------------------------------------------------------

PLACEHOLDER = re.compile(
    r"(?ix)^(?:"
    r"[x#]{1,3}(?:[.,][x#]{1,3})?\s?%"          # XX.XX%  ##%
    r"|[+\-]?0(?:\.0+)?\s?%"                       # 0.00%
    r"|\[[^\]]{1,60}\]|\{[^}]{1,60}\}|<<[^>]{1,60}>>"  # [Fund name]  {nav}  <<date>>
    r"|dd[/.-]mm[/.-](?:yy){1,2}|mm[/.-]dd[/.-](?:yy){1,2}|month\s+yyyy|yyyy-mm-dd"
    r"|x{2,}|tbd|n/?a"
    r")$")
FIGURE = re.compile(r"^[+\-−(]?\s?(?:[A-Z]{3}\s?)?[$€£¥]?\s?\d[\d,]*(?:\.\d+)?\s?%?\)?$")
DATE_WORDS = re.compile(r"(?i)\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b"
                        r"|\b\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s+\d{4}\b"
                        r"|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b"
                        r"|\b(?:january|february|march|april|may|june|july|august|september|october|november|"
                        r"december)\s+\d{4}\b")

# What the label next to a value says, mapped to the catalogue. Read top to bottom; the first
# pattern that matches wins, so the specific labels come before the general ones.
LABELS: tuple[tuple[str, str], ...] = (
    (r"since\s+incep|inception\s+to\s+date|\bsi\b|cumulative", "since_inception_return"),
    (r"year\s*to\s*date|\bytd\b", "ytd_return"),
    (r"(?:1|one)[\s-]*(?:yr|year)|12[\s-]*months?|trailing", "trailing_twelve_month_return"),
    (r"(?:this|1|one)\s*month|\bmtd\b|monthly\s+return|month\s+return", "month_return"),
    (r"annuali[sz]ed\s+(?:return|performance)|cagr", "annualized_return"),
    (r"volatility|std\.?\s*dev", "annualized_volatility"),
    (r"sharpe", "sharpe_ratio"),
    (r"sortino", "sortino_ratio"),
    (r"max(?:imum)?\.?\s*draw\s*down", "maximum_drawdown"),
    (r"cvar|expected\s+shortfall", "cvar_95_daily"),
    (r"\bvar\b|value\s+at\s+risk", "var_95_daily"),
    (r"win\s+rate|positive\s+months|hit\s+rate", "win_rate"),
    (r"risk[\s-]*free", "risk_free_annual_rate"),
    (r"\bnav\b|net\s+asset|fund\s+size|aum|assets\s+under|portfolio\s+value", "nav"),
    (r"(?:no\.?|number)\s+of\s+(?:holdings|positions|stocks)|holdings\s+count", "positions"),
    (r"incep(?:tion)?\s+date|launch\s+date|start\s+date", "history_start_date"),
    (r"as\s+(?:of|at)|data\s+as|valuation\s+date|report(?:ing)?\s+date|month\s+end", "period_end"),
    (r"base\s+currency|reporting\s+currency|currency", "reporting_currency"),
)
TABLE_HEADERS: tuple[tuple[str, str], ...] = (
    (r"holding|security|stock|company|name|position|issuer", "top_holdings"),
    (r"sector|industry|region|country|allocation", "sectors"),
    (r"jan|feb|year|ytd|month", "history_display"),
)


def _label_binding(label: str) -> str | None:
    lowered = label.lower()
    for pattern, name in LABELS:
        if re.search(pattern, lowered) and name in bindings.CATALOGUE:
            return name
    return None


def _figure_kind(text: str) -> str | None:
    cleaned = text.strip()
    if PLACEHOLDER.match(cleaned):
        return "percent" if "%" in cleaned else ("date" if re.search(r"(?i)dd|mm|yy|month|date|as\s*(?:of|at)", cleaned) else "text")
    if cleaned in ("-", "–", "—"):
        return "number"  # a dash where a figure is not available yet (the inception row)
    if DATE_WORDS.search(cleaned):
        return "date"
    if FIGURE.match(cleaned):
        return "percent" if cleaned.endswith("%") or cleaned.endswith("%)") else "number"
    return None


def _neighbours(span: Span, spans: Sequence[Span]) -> list[tuple[float, Span]]:
    """Text a reader would take as this value's label: to its left on the line, or just above it."""
    found = []
    for other in spans:
        if other is span or other.page != span.page or _figure_kind(other.text):
            continue
        if abs(other.y - span.y) <= 0.4 * span.size and other.right <= span.x + 1:
            found.append((span.x - other.right, other))
        elif 0 < other.y - span.y <= 2.6 * max(span.size, other.size) and (
                other.x - 2 <= span.x + span.width / 2 <= other.right + 2 or abs(other.x - span.x) < 6
                or abs(other.right - span.right) < 6):
            found.append((10.0 + 3 * (other.y - span.y), other))
    return sorted(found, key=lambda pair: pair[0])[:3]


def _snapshot_matches(text: str, ctx: bindings.Context | None) -> list[tuple[str, dict[str, Any]]]:
    """Catalogue figures that print as exactly this text in one of the usual formats."""
    if ctx is None:
        return []
    from .checks import dates_in, parse_figure
    matches = []
    parsed = parse_figure(text.replace("(", "-").replace(")", "").replace("$", "").strip())
    found_dates = dates_in(text)
    for name, binding in bindings.CATALOGUE.items():
        if binding.kind != "scalar":
            continue
        value = ctx.scalars.get(name)
        if value is None or isinstance(value, bool):
            continue
        if binding.unit in ("ratio", "number", "money") and parsed is not None:
            printed, half_unit, is_percent = parsed
            expected = float(value) * (100 if binding.unit == "ratio" and is_percent else 1)
            if abs(abs(printed) - abs(expected)) <= half_unit + 1e-9:
                digits = len(text.split(".")[1].rstrip("%) ")) if "." in text else 0
                kind = "percent" if binding.unit == "ratio" else ("money" if binding.unit == "money" else "number")
                fmt: dict[str, Any] = {"kind": kind, "digits": digits}
                if text.strip().startswith("+"):
                    fmt["signed"] = True
                if " %" in text:
                    fmt["suffix"] = " %"
                matches.append((name, fmt))
        elif binding.unit == "date" and found_dates:
            try:
                if date.fromisoformat(str(value)[:10]) in found_dates:
                    matches.append((name, {"kind": "date", "style": _date_style(text)}))
            except ValueError:
                continue
    return matches


_STYLE_PIPES = {"short": "date_short", "long": "date_long", "month_day_year": "date_mdy",
                "month_year": "month_year", "iso": "date_iso"}
DATE_ANY = re.compile(r"(?i)\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}\s+[a-z]{3,9}\.?,?\s+\d{4}\b"
                      r"|\b[a-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}\b|\b[a-z]{3,9}\s+\d{4}\b")


def date_template(text: str, ctx: bindings.Context | None) -> str | None:
    """A phrase with dates in it, as a spec template: 'from 20 Jan 2025 to 30 Sep 2026' ->
    'from {history_start_date|date_short} to {period_end|date_short}'."""
    from .checks import dates_in
    matches = [m for m in DATE_ANY.finditer(text) if dates_in(m.group(0))]
    if not matches or (len(matches) == 1 and matches[0].group(0).strip() == text.strip()):
        return None
    preferred = ("period_end", "history_start_date", "data_as_of")
    out, last = [], 0
    for index, match in enumerate(matches):
        found = dates_in(match.group(0))[0]
        name = None
        if ctx is not None:
            name = next((n for n in preferred if str(ctx.scalars.get(n) or "")[:10] == found.isoformat()), None)
        if name is None:
            name = "history_start_date" if len(matches) > 1 and index == 0 else "period_end"
        out.append(text[last:match.start()] + "{" + name + "|" + _STYLE_PIPES[_date_style(match.group(0))] + "}")
        last = match.end()
    return "".join(out) + text[last:]


def _date_style(text: str) -> str:
    if re.search(r"\d{4}-\d{2}-\d{2}", text):
        return "iso"
    if re.search(r"(?i)^[a-z]+\s+\d{4}$", text.strip()):
        return "month_year"
    if re.search(r"(?i)\d{1,2}\s+[a-z]{3,}\s+\d{4}", text):
        return "long" if re.search(r"(?i)\d{1,2}\s+[a-z]{4,}\s+\d{4}", text) else "short"
    if re.search(r"(?i)[a-z]+\s+\d{1,2},?\s+\d{4}", text):
        return "month_day_year"
    return "long"


@dataclass
class Field:
    field_id: str
    page: int
    text: str
    example_text: str | None
    box: list[float]
    x: float
    y: float
    size: float
    font: str
    color: str
    align: str
    kind: str
    label: str | None
    candidates: list[dict[str, Any]]
    reason: str
    room: float | None = None  # free width on its line, for a value drawn where only a placeholder was

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _same_place(a: Span, b: Span) -> bool:
    return a.page == b.page and abs(a.y - b.y) <= 0.5 * max(a.size, 1) and (
        abs(a.x - b.x) <= 1.5 or abs(a.right - b.right) <= 1.5 or abs((a.x + a.right) - (b.x + b.right)) <= 3)


def _align(span: Span, spans: Sequence[Span]) -> str:
    """Right when the span's right edge lines up with nearby figures in its column (a figures column), else left."""
    if not _figure_kind(span.text):
        return "left"
    near = [o for o in spans if o is not span and o.page == span.page
            and 0.5 * span.size < abs(o.y - span.y) <= 6 * span.size]
    rights = sum(1 for o in near if abs(o.right - span.right) < 0.8 and _figure_kind(o.text))
    lefts = sum(1 for o in near if abs(o.x - span.x) < 0.8)
    return "right" if rights and rights >= lefts else "left"


def _room(span: Span, spans: Sequence[Span], align: str, page_width: float) -> float:
    """How wide a value at this span's anchor can print before it meets other text or the page margin."""
    line = [o for o in spans if o is not span and o.page == span.page and abs(o.y - span.y) <= 0.5 * span.size]
    after = min([o.x for o in line if o.x >= span.right - 0.5] or [page_width - 24]) - span.x - 4
    before = span.right - max([o.right for o in line if o.right <= span.x + 0.5] or [24]) - 4
    if align == "right":
        return round(max(before, span.right - span.x), 2)
    if align == "center":
        return round(max(2 * min(after - span.box[2] / 2, before - span.box[2] / 2), span.box[2]), 2)
    return round(max(after, span.right - span.x), 2)


def _row_mates(words: Sequence[Span], figures: Sequence[Span]) -> list[Span]:
    """Words on a figure's line, to its left, chained word to word (name, sector, weight)."""
    found: list[Span] = []
    anchors = list(figures)
    while True:
        more = [span for span in words if span not in found and any(
            a.page == span.page and abs(a.y - span.y) <= 0.3 * span.size and abs(a.size - span.size) <= 0.5
            and 0 < a.x - span.right <= 320 for a in anchors)]
        if not more:
            return found
        found += more
        anchors += more


def variable_fields(template: Inventory, example: Inventory | None = None,
                    ctx: bindings.Context | None = None) -> list[Field]:
    """The values a renderer must fill each month, with candidate bindings for each."""
    t_spans = template.spans()
    fields: list[Field] = []
    if example is not None:
        e_spans = example.spans()
        used: set[int] = set()
        pairs: list[tuple[Span | None, Span]] = []
        worded: list[tuple[Span, Span]] = []
        for span in e_spans:
            twin = next((t for i, t in enumerate(t_spans) if i not in used and _same_place(t, span)), None)
            if twin is not None:
                used.add(t_spans.index(twin))
                if twin.text == span.text:
                    continue  # template text
                if not (_figure_kind(span.text) or _figure_kind(twin.text)):
                    if len(span.text) <= 60 and _norm(twin.text) not in _norm(span.text) and _norm(
                            span.text) not in _norm(twin.text):
                        worded.append((twin, span))  # different words: a value only if a figure shares its line
                    continue  # otherwise template words the example merely re-flowed
            elif not _figure_kind(span.text) and any(
                    t.page == span.page and abs(t.y - span.y) <= 0.3 * span.size
                    and t.x - 1 <= span.x and span.right <= t.right + 1 for t in t_spans):
                continue  # a piece of a template phrase the example's PDF split differently
            pairs.append((twin, span))
        # A holding's name beside its changed weight is a table cell, not re-flowed template words.
        figures = [shown for _, shown in pairs if _figure_kind(shown.text)]
        mates = _row_mates([shown for _, shown in worded], figures)
        pairs += [(twin, shown) for twin, shown in worded if shown in mates]
        pairs.sort(key=lambda pair: (pair[1].page, -pair[1].y, pair[1].x))
        context_spans = e_spans
    else:
        # No example to compare with: the values are the placeholders and figures left in the
        # document, plus the words sharing a line with them (a holding's name beside its weight),
        # which the table finder needs as cells.
        figures = [span for span in t_spans if _figure_kind(span.text)]
        mates = _row_mates([span for span in t_spans if not _figure_kind(span.text) and len(span.text) <= 60],
                           figures)
        pairs = [(span, span) for span in sorted(figures + mates, key=lambda s: (s.page, -s.y, s.x))]
        context_spans = t_spans
    for index, (in_template, shown) in enumerate(pairs):
        kind = _figure_kind(shown.text) or (_figure_kind(in_template.text) if in_template else None) or (
            "commentary" if len(shown.text) > 60 else "text")
        labels = _neighbours(shown, context_spans)
        label = labels[0][1].text if labels else None
        candidates: list[dict[str, Any]] = []
        for name, fmt in _snapshot_matches(shown.text, ctx):
            candidates.append({"binding": name, "format": fmt, "confidence": "matched",
                               "reason": f"prints {shown.text!r}, the snapshot's {bindings.CATALOGUE[name].label}"})
        for _, neighbour in labels:
            name = _label_binding(neighbour.text)
            if name and not any(c["binding"] == name for c in candidates):
                unit = bindings.CATALOGUE[name].unit
                if kind == "percent" and unit != "ratio" or kind == "date" and unit != "date":
                    continue
                candidates.append({"binding": name, "format": _default_format(name, shown.text),
                                   "confidence": "label", "reason": f"labelled {neighbour.text!r}"})
        template_text = date_template(shown.text, ctx) if kind == "date" else None
        if template_text:
            candidates.insert(0, {"binding": "template", "template": template_text, "confidence":
                                  "matched" if ctx is not None else "guess",
                                  "reason": "a phrase with dates in it, kept as written with its dates bound"})
        if kind == "date" and not candidates:
            candidates.append({"binding": "period_end", "format": {"kind": "date", "style": _date_style(shown.text)},
                               "confidence": "guess", "reason": "the only date on a factsheet is usually its month-end"})
        reason = ("differs from the template" if example is not None and in_template is not None
                  else "only in the completed example" if example is not None else "a placeholder in the template")
        source = in_template or shown
        align = _align(shown, context_spans)
        page_width = (example or template).pages[shown.page].size[0]
        fields.append(Field(
            field_id=f"p{shown.page + 1}.f{index + 1}", page=shown.page,
            text=in_template.text if in_template else "", example_text=shown.text if example is not None else None,
            box=_union(in_template.box if in_template else None, shown.box), x=source.x, y=source.y,
            size=source.size, font=source.font, color=source.color, align=align,
            kind=kind, label=label, candidates=candidates, reason=reason,
            room=None if example is not None else _room(shown, context_spans, align, page_width)))
    return fields


def _union(a: list[float] | None, b: list[float]) -> list[float]:
    if a is None:
        return b
    x0, y0 = min(a[0], b[0]), min(a[1], b[1])
    x1, y1 = max(a[0] + a[2], b[0] + b[2]), max(a[1] + a[3], b[1] + b[3])
    return [round(x0, 2), round(y0, 2), round(x1 - x0, 2), round(y1 - y0, 2)]


def _default_format(name: str, text: str) -> dict[str, Any]:
    unit = bindings.CATALOGUE[name].unit
    digits = len(text.split(".")[1].rstrip("%) ")) if "." in text else 2
    digits = digits if digits <= 4 and text.split(".")[-1].rstrip("%) ").isdigit() else 2
    if unit == "ratio":
        fmt: dict[str, Any] = {"kind": "percent", "digits": digits}
        if text.strip().startswith("+"):
            fmt["signed"] = True
        return fmt
    if unit == "money":
        return {"kind": "money", "digits": digits if "." in text else 0}
    if unit == "number":
        return {"kind": "number", "digits": digits if "." in text else 0}
    if unit == "date":
        return {"kind": "date", "style": _date_style(text)}
    return {"kind": "text"}


def _cell_values(name: str, field_name: str, ctx: bindings.Context) -> list[str]:
    """The ways one column of a row binding is usually printed, row by row."""
    rows = ctx.rows.get(name) or []
    unit = bindings.CATALOGUE[name].field_units.get(field_name)
    variants: list[str] = []
    for row in rows:
        value = row.get(field_name)
        if value is None:
            variants.append("")
            continue
        if unit == "ratio" or (field_name in ("monthly_return", "cumulative_return", "weight")
                               and isinstance(value, (int, float))):
            variants.append(f"{float(value) * 100:.2f}%")
        elif isinstance(value, float):
            variants.append(f"{value:.2f}")
        else:
            variants.append(str(value))
    return variants


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9.%-]", "", text.lower().replace("−", "-").replace(" %", "%"))


def tables(fields: Sequence[Field], spans: Sequence[Span], ctx: bindings.Context | None = None
           ) -> list[dict[str, Any]]:
    """Blocks of three or more rows of values at a regular pitch with the same columns: a table to draw as one.

    Text cells (holding and sector names) count as cells. When a snapshot is supplied,
    each column is matched against every row binding's columns; otherwise the header
    words choose the binding.
    """
    cells = [f for f in fields if f.kind != "commentary"]
    found = []
    for page in sorted({f.page for f in cells}):
        rows: list[list[Field]] = []
        for item in sorted((f for f in cells if f.page == page), key=lambda f: (-f.y, f.x)):
            if rows and abs(rows[-1][0].y - item.y) <= 0.35 * item.size:
                rows[-1].append(item)
            else:
                rows.append([item])
        rows = [sorted(r, key=lambda f: f.x) for r in rows if len(r) >= 2]
        block: list[list[Field]] = []

        def shape(row: list[Field]) -> list[float]:
            return [round((f.box[0] + f.box[2]) if f.align == "right" else f.box[0]) for f in row]

        def close() -> None:
            if len(block) >= 3:
                found.append(_table(block, spans, ctx))

        for row in rows:
            if block:
                pitch = block[-1][0].y - row[0].y
                previous = block[-2][0].y - block[-1][0].y if len(block) > 1 else pitch
                same = len(row) == len(block[-1]) and all(
                    abs(a - b) <= 4 for a, b in zip(shape(row), shape(block[-1])))
                if same and pitch > 0 and abs(pitch - previous) <= 0.3 * previous:
                    block.append(row)
                    continue
                close()
            block = [row]
        close()
    return found


def _table(block: list[list[Field]], spans: Sequence[Span], ctx: bindings.Context | None) -> dict[str, Any]:
    page = block[0][0].page
    pitches = [a[0].y - b[0].y for a, b in zip(block, block[1:])]
    pitch = sum(pitches) / len(pitches)
    columns = []
    for index in range(len(block[0])):
        column = [row[index] for row in block]
        columns.append({"x": round(min(f.box[0] for f in column), 2),
                        "right": round(max(f.box[0] + f.box[2] for f in column), 2),
                        "align": column[0].align, "kind": column[0].kind,
                        "cells": [f.example_text or f.text for f in column],
                        "fields": [f.field_id for f in column]})
    top = block[0][0].y
    header = [s for s in spans if s.page == page and 0 < s.y - top <= 2.5 * pitch
              and columns[0]["x"] - 4 <= s.x <= columns[-1]["right"] + 4]
    binding, matched = None, {}
    if ctx is not None:
        best = 0
        for name, entry in bindings.CATALOGUE.items():
            if entry.kind != "rows":
                continue
            score, mapping = 0, {}
            for index, column in enumerate(columns):
                printed = [_norm(c) for c in column["cells"]]
                for field_name in entry.fields:
                    values = [_norm(v) for v in _cell_values(name, field_name, ctx)]
                    hits = _aligned_hits(printed, values)
                    if hits >= max(2, len(printed) // 2) and hits > mapping.get(index, ("", 0))[1]:
                        mapping[index] = (field_name, hits)
            score = sum(h for _, h in mapping.values())
            if score > best:
                best, binding, matched = score, name, {i: f for i, (f, _) in mapping.items()}
    if binding is None:
        words = " ".join(s.text for s in header).lower()
        binding = next((name for pattern, name in TABLE_HEADERS if re.search(pattern, words)), None)
    for index, column in enumerate(columns):
        column["field"] = matched.get(index)
    return {"page": page, "rows": len(block), "pitch": round(pitch, 2), "top": round(top + 0.72 * block[0][0].size, 2),
            "first_baseline": round(top, 2), "size": block[0][0].size, "font": block[0][0].font,
            "header": [s.text for s in sorted(header, key=lambda s: (-s.y, s.x))][:16],
            "color": block[0][0].color, "header_color": next(
                (s.color for s in header if s.y - top <= 1.5 * pitch), None),
            "binding": binding, "matched_with_snapshot": bool(matched), "columns": columns}


def _aligned_hits(printed: list[str], values: list[str]) -> int:
    """How many printed cells equal the binding's values at some fixed row offset (a table may skip rows)."""
    if not values:
        return 0
    best = 0
    for offset in range(-len(printed) + 1, len(values)):
        hits = sum(1 for i, p in enumerate(printed) if 0 <= i + offset < len(values) and p and p == values[i + offset])
        best = max(best, hits)
    # Tables printed in a different order (largest first) still match as a set.
    return max(best, len(set(printed) & set(values)) if len(set(printed)) > 2 else 0)


def mark_static(fields: Sequence[Field], others: Sequence[Inventory], skip: set[str] = frozenset()) -> None:
    """Words the example prints identically in every other month are design labels, not values.

    A renderer drew them (a table heading, a section title), so the spec draws them
    as fixed text; a value that changes from month to month stays bound. A heading
    that moves with a growing table still counts as a label. Table cells never do:
    the same five holdings two months running are still data.
    """
    if not others:
        return
    spans = [inv.spans() for inv in others]
    for item in fields:
        shown = item.example_text or item.text
        if item.kind != "text" or not shown or item.field_id in skip:
            continue
        if all(any(o.text == shown and o.page == item.page and abs(o.y - item.y) <= 120
                   for o in month) for month in spans):
            item.kind = "label"
            item.reason = "printed the same way in every example month: a design label"


def refine_alignment(fields: Sequence[Field], others: Sequence[Inventory]) -> None:
    """Learn each value's alignment from how it moved between months.

    A value printed left-aligned keeps its left edge when its width changes; a
    right-aligned one keeps its right edge; a centred one its middle. One example
    cannot tell these apart, two months usually can.
    """
    spans = [o for inv in others for o in inv.spans()]
    for item in fields:
        if item.kind in ("label", "chart", "commentary") or not (item.example_text or item.text):
            continue
        shown = item.example_text or item.text
        left, right = item.box[0], item.box[0] + item.box[2]
        votes = {"left": 0, "right": 0, "center": 0}
        for other in spans:
            if (other.page != item.page or abs(other.y - item.y) > 0.3 * item.size or other.text == shown
                    or abs(other.size - item.size) > 0.2 or other.right < left - 2 or other.x > right + 2
                    or abs(other.width - item.box[2]) < 1.0):
                continue
            deltas = {"left": abs(other.x - left), "right": abs(other.right - right),
                      "center": abs((other.x + other.right) / 2 - (left + right) / 2)}
            best = min(deltas, key=deltas.get)
            if deltas[best] < 0.6:
                votes[best] += 1
        if any(votes.values()):
            item.align = max(votes, key=votes.get)


def _inside(box: Sequence[float], area: Sequence[float], margin: float = 14.0) -> bool:
    return (area[0] - 3 * margin <= box[0] and box[0] + box[2] <= area[0] + area[2] + margin
            and area[1] - margin <= box[1] and box[1] + box[3] <= area[1] + area[3] + margin)


def read(template_path: str | Path, example_path: str | Path | None = None,
         snapshot: Mapping[str, Any] | None = None, *, more_examples: Sequence[str | Path] = ()) -> dict[str, Any]:
    """Everything the builder needs to start: both inventories, the variable fields, tables and charts.

    ``snapshot`` is the frozen snapshot for the example's month, when there is one;
    ``more_examples`` are other months' completed factsheets, which separate the
    design's fixed labels from its values.
    """
    template = inventory(template_path)
    example = inventory(example_path) if example_path else None
    ctx = bindings.build_context(snapshot) if snapshot is not None else None
    fields = variable_fields(template, example, ctx)
    spans = (example or template).spans()
    found_tables = tables(fields, spans, ctx)
    for found in found_tables:
        table_geometry(found, (example or template).pages[found["page"]])
    cells = {f for t in found_tables for c in t["columns"] for f in c["fields"]}
    others = [inventory(path) for path in more_examples]
    mark_static(fields, others, cells)
    refine_alignment(fields, others)
    for found in found_tables:
        lookup = {f.field_id: f for f in fields}
        for column in found["columns"]:
            aligns = [lookup[f].align for f in column["fields"]]
            column["align"] = max(set(aligns), key=aligns.count)
    table_boxes = [_table_box(t) for t in found_tables]
    charts = [{"page": p.number, **g} for p in (example or template).pages for g in p.graphics
              if not any(_overlaps(g["box"], box) for page, box in table_boxes if page == p.number)]
    for item in fields:
        if item.field_id not in cells and any(c["page"] == item.page and _inside(item.box, c["box"])
                                              for c in charts) and item.kind in ("text", "number", "label", "date"):
            if not item.candidates or item.kind != "date":
                item.kind = "chart"
                item.reason = "an axis or legend label inside a chart: the chart component draws it"
    return {
        "template": template, "example": example, "fields": fields, "tables": found_tables,
        "charts": charts, "has_example": example is not None, "matched_with_snapshot": ctx is not None,
        "months_compared": 1 + len(more_examples) if example is not None else 0,
    }


def table_geometry(table: dict[str, Any], page: PageInventory) -> None:
    """Measure a table's frame from the drawing: the header band's fill and the grid's cell edges.

    Adds ``x``, ``top``, ``header_height``, ``body_height``, the header and band colours
    and each column's ``width``, which is what a table operation needs. Leaves the
    estimate from the text alone when the table has no visible frame.
    """
    columns = table["columns"]
    left, right = columns[0]["x"], columns[-1]["right"]
    first = table["first_baseline"]
    pitch = table["pitch"]
    header = None
    for fill in page.fills:
        x, y, w, h = fill["box"]
        if x <= left + 2 and x + w >= right - 2 and first + 0.2 * pitch <= y <= first + 1.6 * pitch + 6 and h < 3 * pitch:
            if header is None or y < header["box"][1]:
                header = fill
    if header is not None:
        x, y, w, h = header["box"]
        edges = sorted({round(e * 2) / 2 for b in page.strokes if abs(b[1] - y) < 0.6 and abs(b[3] - h) < 0.6
                        for e in (b[0], b[0] + b[2])})
        table.update({"x": x, "top": round(y + h, 2), "header_height": round(h, 2), "header_fill": header["color"],
                      "frame_width": round(w, 2)})
        inner = [e for e in edges if x - 0.5 <= e <= x + w + 0.5]
        if len(inner) == len(columns) + 1:
            for column, (a, b) in zip(columns, zip(inner, inner[1:])):
                column["width"] = round(b - a, 2)
    else:
        table.update({"x": round(left - 2, 2), "top": round(first + 0.72 * table["size"] + 2, 2),
                      "header_height": round(pitch * 1.2, 2), "frame_width": round(right - left + 4, 2)})
    if not all("width" in c for c in columns):
        # No grid to measure: a left-aligned column starts just before its text, a right-aligned
        # one ends just after it, with the first column's inset as the padding; else the midpoint.
        pad = min(8.0, max(1.0, columns[0]["x"] - table["x"]))

        def bound(a: Mapping[str, Any], b: Mapping[str, Any]) -> float:
            if b["align"] == "left" and b["x"] - pad > a["right"]:
                return round(b["x"] - pad, 2)
            if a["align"] == "right" and a["right"] + pad < b["x"]:
                return round(a["right"] + pad, 2)
            return round((a["right"] + b["x"]) / 2, 2)

        bounds = [table["x"]] + [bound(a, b) for a, b in zip(columns, columns[1:])] + [
            round(table["x"] + table["frame_width"], 2)]
        for column, (a, b) in zip(columns, zip(bounds, bounds[1:])):
            column["width"] = round(b - a, 2)
    table["body_height"] = round(pitch, 2)
    bands = sorted({f["color"] for f in page.fills
                    if f["box"][1] < table["top"] - table["header_height"] and abs(f["box"][3] - pitch) < 0.6
                    and f["box"][0] <= left + 2 and f["box"][0] + f["box"][2] >= right - 2})
    if bands:
        table["band_fills"] = bands


def _table_box(table: Mapping[str, Any]) -> tuple[int, list[float]]:
    x0 = table["columns"][0]["x"]
    x1 = table["columns"][-1]["right"]
    bottom = table["first_baseline"] - table["pitch"] * table["rows"]
    return table["page"], [x0 - 4, bottom, x1 - x0 + 8, table["top"] - bottom + 2 * table["pitch"]]


def _overlaps(a: Sequence[float], b: Sequence[float]) -> bool:
    ix = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    return ix * iy > 0.5 * min(a[2] * a[3], b[2] * b[3])
