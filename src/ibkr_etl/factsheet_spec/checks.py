"""Checks generated from the spec itself, run on every spec-rendered factsheet.

The hand-written validators check where text sits. These read the written PDF
back and check what it says:

- ``printed``: every value the spec drew is in the PDF, at its position, in its font and size;
- ``figures``: every bound figure parses back to the snapshot value within the rounding shown;
- ``truncation``: nothing was cut short with "...";
- ``fit``: every text sits inside the page and every table cell inside its column;
- ``overlap``: no two drawn texts collide, and none sits on a chart or diagram;
- ``stale_dates``: no date later than the report is left anywhere on the page;
- ``currency``: the reporting currency is stated (warning);
- ``spelling``: one spelling per label in each table column (warning).

A ``fail`` stops the render and deletes the file; a ``warn`` is shown to the
owner and the checker agent, who decide whether it matters.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from pypdf import PdfReader
from reportlab.pdfbase.pdfmetrics import stringWidth

from . import bindings
from .paint import scan_page

POSITION_TOLERANCE = 0.25
SIZE_TOLERANCE = 0.06
_MONTHS = ("january february march april may june july august september october november december").split()
_SHORT = [m[:3] for m in _MONTHS]
_DATE_PATTERNS = (
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),
    re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b"),
    re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})\b"),
)
_FIGURE = re.compile(r"^[+\-−]?\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s?(%?)$")


def _check(check_id: str, status: str, detail: str, **evidence: Any) -> dict[str, Any]:
    return {"id": check_id, "status": status, "detail": detail, **({"evidence": evidence} if evidence else {})}


def _month(name: str) -> int | None:
    lowered = name.lower().rstrip(".")
    if lowered in _MONTHS:
        return _MONTHS.index(lowered) + 1
    if lowered[:3] in _SHORT and len(lowered) <= 4:
        return _SHORT.index(lowered[:3]) + 1
    return None


def dates_in(text: str) -> list[date]:
    found = []
    for pattern_index, pattern in enumerate(_DATE_PATTERNS):
        for match in pattern.finditer(text):
            try:
                if pattern_index == 0:
                    found.append(date(int(match.group(1)), int(match.group(2)), int(match.group(3))))
                elif pattern_index == 1:
                    month = _month(match.group(2))
                    if month:
                        found.append(date(int(match.group(3)), month, int(match.group(1))))
                else:
                    month = _month(match.group(1))
                    if month:
                        found.append(date(int(match.group(3)), month, int(match.group(2))))
            except ValueError:
                continue
    return found


def parse_figure(text: str) -> tuple[float, float, bool] | None:
    """(value, half the last printed unit, is a percentage) for a printed figure like '+12.34%' or '1,234'."""
    cleaned = text.replace("−", "-").replace(" %", "%").strip()
    match = _FIGURE.match(cleaned)
    if not match:
        return None
    digits = len(match.group(2) or "")
    value = float(cleaned.rstrip("%").replace(",", "").replace(" ", ""))
    return value, 0.5 * 10 ** (-digits), bool(match.group(3))


def _bbox(item: Mapping[str, Any]) -> tuple[float, float, float, float]:
    width = stringWidth(item["text"], item["font"], item["size"])
    return (item["x"], item["y"] - 0.21 * item["size"], item["x"] + width, item["y"] + 0.72 * item["size"])


def _intersects(a: tuple[float, ...], b: tuple[float, ...], margin: float = 0.3) -> bool:
    return a[0] < b[2] - margin and b[0] < a[2] - margin and a[1] < b[3] - margin and b[1] < a[3] - margin


def run(path: str | Path, spec: Mapping[str, Any], ctx: bindings.Context, drawn: Any) -> dict[str, Any]:
    reader = PdfReader(str(path))
    scans = [scan_page(page) for page in reader.pages]
    sizes = [(float(p.mediabox.width), float(p.mediabox.height)) for p in reader.pages]
    texts = drawn.texts
    results: list[dict[str, Any]] = []

    missing = []
    for item in texts:
        runs = scans[item["page"]].runs
        if not any(run.text == item["text"].strip() and abs(run.x - item["x"]) <= POSITION_TOLERANCE
                   and abs(run.y - item["y"]) <= POSITION_TOLERANCE and abs(run.size - item["size"]) <= SIZE_TOLERANCE
                   and (run.font or "").endswith(item["font"]) for run in runs):
            missing.append(item["id"])
    results.append(_check("printed", "fail" if missing else "pass",
                          f"{len(missing)} drawn values are not in the PDF where the design puts them: "
                          + ", ".join(missing[:5]) if missing else f"All {len(texts)} drawn values are in place.",
                          **({"missing": missing} if missing else {})))

    wrong = []
    checked = 0
    for item in texts:
        value = item.get("value")
        if not isinstance(value, Mapping) or not ("bind" in value or "field" in value):
            continue
        if "bind" in value:
            binding = bindings.CATALOGUE.get(value["bind"])
            unit = binding.unit if binding is not None else None
            raw = ctx.scalars.get(value["bind"])
        else:
            table = bindings.CATALOGUE.get(item.get("rows", ""))
            unit = table.field_units.get(value["field"]) if table is not None else None
            raw = item.get("raw")
        if unit not in ("ratio", "number", "money"):
            continue
        parsed = parse_figure(item["text"])
        if raw is None:
            if item["text"] != str((value.get("format") or {}).get("none", "N/A")):
                wrong.append(item["id"])
            continue
        if parsed is None:
            wrong.append(item["id"])
            continue
        printed, half_unit, is_percent = parsed
        expected = float(raw)
        fmt = value.get("format") or {}
        if fmt.get("negate_abs"):
            expected = -abs(expected)
        if fmt.get("abs"):
            expected = abs(expected)
        if unit == "ratio" and (is_percent or fmt.get("kind") == "percent"):
            expected *= 100
        checked += 1
        if abs(printed - expected) > half_unit + 1e-9:
            wrong.append(item["id"])
    results.append(_check("figures", "fail" if wrong else "pass",
                          f"{len(wrong)} printed figures do not match the snapshot: " + ", ".join(wrong[:5])
                          if wrong else f"All {checked} bound figures match the snapshot.",
                          **({"wrong": wrong} if wrong else {})))

    literals = repr(spec)
    cut = [item["id"] for item in texts
           if item["text"].endswith("...") and item["text"].strip(". ") and item["text"] not in literals]
    results.append(_check("truncation", "fail" if cut else "pass",
                          f"{len(cut)} values were cut short: " + ", ".join(cut[:5]) if cut
                          else "Nothing was cut short."))

    outside = []
    for item in texts:
        x0, y0, x1, y1 = _bbox(item)
        width, height = sizes[item["page"]]
        if x0 < -0.5 or y0 < -0.5 or x1 > width + 0.5 or y1 > height + 0.5:
            outside.append(item["id"])
        if "cell" in item and stringWidth(item["text"], item["font"], item["size"]) > item["cell"][0] + 0.05:
            outside.append(item["id"])
    results.append(_check("fit", "fail" if outside else "pass",
                          f"{len(outside)} values run outside their space: " + ", ".join(outside[:5]) if outside
                          else "Every value fits its space."))

    collisions = []
    by_page: dict[int, list[Mapping[str, Any]]] = {}
    for item in texts:
        by_page.setdefault(item["page"], []).append(item)
    for page, items in by_page.items():
        boxes = [(_bbox(item), item) for item in items]
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                if _intersects(boxes[i][0], boxes[j][0]):
                    collisions.append(f"{boxes[i][1]['id']} / {boxes[j][1]['id']}")
        for area in drawn.boxes:
            if area["page"] != page or not area.get("component"):
                continue
            x, y, w, h = area["box"]
            for bbox, item in boxes:
                if _intersects(bbox, (x, y, x + w, y + h), margin=1.0):
                    collisions.append(f"{item['id']} / {area['id']}")
    results.append(_check("overlap", "fail" if collisions else "pass",
                          f"{len(collisions)} overlaps: " + "; ".join(collisions[:5]) if collisions
                          else "No values overlap each other or a chart."))

    period_end = date.fromisoformat(str(ctx.scalars["period_end"]))
    stale = []
    for page_number, scan in enumerate(scans):
        for run in scan.runs:
            for found in dates_in(run.text):
                if found > period_end:
                    stale.append({"page": page_number + 1, "text": run.text[:60]})
    results.append(_check("stale_dates", "fail" if stale else "pass",
                          f"{len(stale)} dates after the report's month-end are still on the page"
                          if stale else "No date after the month-end is left on the page.",
                          **({"found": stale[:10]} if stale else {})))

    currency = str(ctx.scalars.get("reporting_currency") or "")
    stated = any(currency in run.text for scan in scans for run in scan.runs)
    results.append(_check("currency", "pass" if stated else "warn",
                          f"The reporting currency ({currency}) is stated." if stated
                          else f"The factsheet never states its reporting currency ({currency})."))

    spellings = []
    for page_spec in spec["pages"]:
        for layer in page_spec["layers"]:
            for op in layer.get("ops", []) if layer["kind"] == "overlay" else []:
                if op.get("op") != "table":
                    continue
                columns: dict[int, dict[str, set[str]]] = {}
                for item in texts:
                    match = re.fullmatch(re.escape(op["id"]) + r"\.r(\d+)\.c(\d+)", item["id"])
                    if not match or match.group(1) == "0" or re.search(r"\d", item["text"]):
                        continue
                    key = re.sub(r"[^a-z0-9]", "", item["text"].lower())
                    columns.setdefault(int(match.group(2)), {}).setdefault(key, set()).add(item["text"])
                for column, keys in columns.items():
                    for variants in keys.values():
                        if len(variants) > 1:
                            spellings.append(f"{op['id']} column {column + 1}: " + " / ".join(sorted(variants)))
    results.append(_check("spelling", "warn" if spellings else "pass",
                          "Labels printed under two spellings: " + "; ".join(spellings[:3]) if spellings
                          else "One spelling per label."))

    status = "fail" if any(r["status"] == "fail" for r in results) else (
        "warn" if any(r["status"] == "warn" for r in results) else "pass")
    return {"status": status, "checks": results}
