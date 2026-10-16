"""The first draft of a design spec, written by code from what the template reader found.

The draft is deliberately plain: every value the reader bound becomes a text
operation where the example printed it, every table the reader measured becomes
a table operation with the frame it measured, every chart becomes the chart
component in the chart's box, and every fixed label the example printed becomes
fixed text. Whatever the reader could not bind is left out and listed as an open
question. The builder agent then improves the draft by spec edits only, judged by
the comparison against the completed example; it never writes code.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from . import bindings
from .reader import Field
from .spec import SPEC_VERSION, validate_spec

CONFIDENCE = {"matched": 0, "label": 1, "guess": 2}
ROLE_BY_BINDING = {"period_end": "date", "data_as_of": "date", "display_date": "date"}


def standard_font(name: str) -> str:
    """The closest standard PDF font to an embedded one (the spec vocabulary draws in standard fonts)."""
    lowered = (name or "").split("+")[-1].lower()
    bold = any(word in lowered for word in ("bold", "black", "heavy", "semibold", "demi"))
    italic = "italic" in lowered or "oblique" in lowered
    if any(word in lowered for word in ("times", "serif", "georgia", "garamond", "cambria", "minion", "palatino")) \
            and "sans" not in lowered:
        family = "Times"
        return {(False, False): "Times-Roman", (True, False): "Times-Bold", (False, True): "Times-Italic",
                (True, True): "Times-BoldItalic"}[(bold, italic)]
    family = "Courier" if any(word in lowered for word in ("courier", "mono", "consolas")) else "Helvetica"
    suffix = "-BoldOblique" if bold and italic else "-Bold" if bold else "-Oblique" if italic else ""
    return family + suffix


def _slug(text: str, used: set[str], prefix: str) -> str:
    base = prefix + "." + (re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "value")
    candidate, n = base, 2
    while candidate in used:
        candidate, n = f"{base}_{n}", n + 1
    used.add(candidate)
    return candidate


def _anchor(item: Field) -> float:
    x, width = item.box[0], item.box[2]
    return round(x + width if item.align == "right" else x + width / 2 if item.align == "center" else x, 2)


def best_candidate(item: Field) -> dict[str, Any] | None:
    ranked = sorted(item.candidates, key=lambda c: CONFIDENCE.get(c.get("confidence"), 9))
    return ranked[0] if ranked else None


def value_for(candidate: Mapping[str, Any]) -> Any:
    if candidate["binding"] == "template":
        return candidate["template"]
    fmt = dict(candidate.get("format") or {})
    if fmt.get("kind") == "date":
        return "{" + candidate["binding"] + "|" + {
            "short": "date_short", "long": "date_long", "month_day_year": "date_mdy", "month_year": "month_year",
            "iso": "date_iso"}.get(fmt.get("style", "long"), "date_long") + "}"
    if fmt.get("kind") in (None, "text"):
        return {"bind": candidate["binding"]}
    fmt.setdefault("none", "N/A")
    return {"bind": candidate["binding"], "format": fmt}


def _literal(text: str) -> str:
    """Fixed text, with anything that looks like a placeholder broken so it prints as written."""
    return bindings._PLACEHOLDER.sub(lambda m: m.group(0).replace("{", "{​"), text)


def _text_op(item: Field, op_id: str, value: Any, role: str, fit: bool) -> dict[str, Any]:
    op: dict[str, Any] = {"id": op_id, "op": "text", "role": role, "x": _anchor(item), "y": round(item.y, 2),
                          "align": item.align, "font": standard_font(item.font), "size": round(item.size, 2),
                          "color": item.color, "value": value}
    if fit:
        # Measured from the completed example when there is one; else as far as the line has room.
        width = item.room if item.room else max(item.box[2] * 1.6, item.box[2] + 20)
        op["fit"] = {"width": round(width, 2),
                     "min_size": round(max(3.0, item.size * 0.7), 2)}
    return op


def _header_lines(table: Mapping[str, Any], spans: Sequence[Any], column: Mapping[str, Any], left: float,
                  width: float) -> tuple[str, str]:
    top, height = table["top"], table["header_height"]
    inside = [s for s in spans if s.page == table["page"] and top - height - 1 <= s.y <= top + 1
              and left - 1 <= (s.x + s.right) / 2 <= left + width + 1]
    lines = [s.text for s in sorted(inside, key=lambda s: (-s.y, s.x))]
    align = "left"
    if inside:
        middle = sum((s.x + s.right) / 2 for s in inside) / len(inside)
        align = "center" if abs(middle - (left + width / 2)) < 0.15 * width else (
            "right" if middle > left + width / 2 else "left")
    return "\n".join(lines), align


# Column headers that name a row field in other words ("Holding" for the company's name).
COLUMN_WORDS = (
    (r"\b(?:ticker|symbol|code)\b", "symbol"),
    (r"compan|holding|security|stock|issuer|name|position|investment", "company_name"),
    (r"industry", "sector"),
    (r"country|region|market", "region"),
)


def _column_value(table: Mapping[str, Any], column: Mapping[str, Any], header: str) -> dict[str, Any] | None:
    binding = bindings.CATALOGUE.get(table.get("binding") or "")
    if binding is None:
        return None
    name = column.get("field")
    if name is None:
        words = header.lower()
        for field_name in binding.fields:
            if field_name.replace("_", "-") in words.replace(" ", "-") or field_name.replace("_", " ") in words:
                name = field_name
        if name is None and column["kind"] == "percent" and "weight" in binding.fields:
            name = "weight"
        for pattern, field_name in COLUMN_WORDS:
            if name is None and field_name in binding.fields and re.search(pattern, words):
                name = field_name
    if name is None or name not in binding.fields:
        return None
    value: dict[str, Any] = {"field": name}
    if binding.field_units.get(name) == "ratio" and table["binding"] != "history_display":
        cells = [c for c in column["cells"] if "." in c]
        digits = len(cells[0].split(".")[1].rstrip("% ")) if cells else 2
        value["format"] = {"kind": "percent", "digits": digits}
    return value


def _table_op(table: Mapping[str, Any], op_id: str, spans: Sequence[Any], heading: Field | None,
              total: list[str] | None) -> tuple[dict[str, Any] | None, list[str]]:
    questions = []
    columns = []
    cursor = float(table["x"])
    for index, column in enumerate(table["columns"]):
        width = float(column["width"])
        header, header_align = _header_lines(table, spans, column, cursor, width)
        value = _column_value(table, column, header)
        if value is None:
            questions.append(f"Which figure goes in column {index + 1} of the table headed "
                             f"{(heading.example_text or heading.text) if heading else header!r}?")
            value = {"field": bindings.CATALOGUE[table["binding"]].fields[0]} if table.get("binding") else None
        if value is None:
            return None, questions
        align = column["align"]
        middle = (column["x"] + column["right"]) / 2
        if abs(middle - (cursor + width / 2)) < 1.0 and abs(column["x"] - cursor) > 3 and abs(
                cursor + width - column["right"]) > 3:
            align = "center"  # every cell sits in the middle of its column
        columns.append({"width": width, "align": align, "header_align": header_align,
                        "header": header, "value": value})
        cursor += width
    first = table["columns"][0]
    padding = 4.0
    if first["align"] == "left":
        padding = round(max(1.0, min(12.0, 2 * (first["x"] - float(table["x"])))), 2)
    op: dict[str, Any] = {
        "id": op_id, "op": "table", "role": table["binding"] or "table", "rows": table["binding"],
        "x": table["x"], "top": table["top"], "columns": columns, "header_height": table["header_height"],
        "body_height": table["body_height"], "font_size": table["size"], "padding": padding, "overflow": "shrink",
    }
    if table["binding"] == "top_holdings":
        op["limit"] = table["rows"]
    style: dict[str, Any] = {}
    if table.get("header_fill"):
        style["header_fill"] = table["header_fill"]
        if table.get("header_color") and table["header_color"] != "#ffffff":
            style["header_text"] = table["header_color"]
    if table.get("color") and table["color"] != "#121925":
        style["text"] = table["color"]
    bands = table.get("band_fills") or []
    if bands:
        style["alt_fill"] = bands[-1]
        if len(bands) > 1:
            style["total_fill"] = bands[0]
    if style:
        op["style"] = style
    if table["binding"] in ("history", "history_display"):
        # A history grows a row a month: keep its bottom where it is and let it grow upward
        # into the free space above, then compress the rows, as the account design does.
        bottom = round(float(table["top"]) - float(table["header_height"]) - table["rows"] * float(table["body_height"]),
                       2)
        gap = round(heading.y - float(table["top"]), 2) if heading is not None else 0.0
        above = [s.y - 0.22 * s.size for s in spans if s.page == table["page"] and s.y > float(table["top"]) + gap + 2
                 and table["x"] - 2 <= s.x <= table["x"] + table.get("frame_width", 0)]
        ceiling = min(above) if above else float(table["top"]) + 6 * float(table["body_height"])
        max_top = round(max(float(table["top"]), ceiling - gap - 10), 2)
        # history_display prints at most the inception row, a gap row and the latest 36 months
        most_rows = 38 if table["binding"] == "history_display" else 60
        room = (max_top - bottom - float(table["header_height"])) / most_rows
        op["grow"] = {"reference_rows": table["rows"], "bottom": bottom, "max_top": max_top,
                      "min_body_height": round(max(4.0, min(0.8 * float(table["body_height"]), room - 0.01)), 2),
                      "heading_gap": gap}
    if heading is not None:
        op["heading"] = {"y": round(heading.y, 2), "x": _anchor(heading), "align": heading.align,
                         "font": standard_font(heading.font), "size": heading.size, "color": heading.color,
                         "value": _literal(heading.example_text or heading.text) if heading.kind == "label"
                         else value_for(best_candidate(heading))}
    if total:
        op["total"] = total
    return op, questions


def draft_spec(found: Mapping[str, Any], *, design_id: str, title: str, currency: str,
               inputs: str = "account") -> dict[str, Any]:
    """A first spec from ``reader.read``'s result, with the questions the reader could not answer."""
    template = found["template"]
    shown = found["example"] or template
    spans = shown.spans()
    fields: list[Field] = list(found["fields"])
    by_id = {f.field_id: f for f in fields}
    used_ids: set[str] = set()
    questions: list[dict[str, Any]] = []
    in_tables = {f for t in found["tables"] for c in t["columns"] for f in c["fields"]}
    consumed: set[str] = set(in_tables)
    pages = []
    for page in template.pages:
        number = page.number
        ops: list[dict[str, Any]] = []
        remove: list[list[float]] = []
        for table in [t for t in found["tables"] if t["page"] == number]:
            heading = next((f for f in sorted(fields, key=lambda f: f.y) if f.page == number
                            and f.field_id not in consumed and f.field_id not in in_tables
                            and (f.kind == "label" or (f.kind == "date" and best_candidate(f)
                                                       and best_candidate(f)["binding"] == "template"))
                            and 0 < f.y - table["top"] <= 24 and abs(f.box[0] - table["x"]) < 40), None)
            total_row = None
            last = min(by_id[c["fields"][-1]].y for c in table["columns"])
            below = [f for f in fields if f.page == number and f.field_id not in consumed
                     and 0 < last - f.y <= 1.4 * table["pitch"]]
            if below and any((f.example_text or f.text).upper() == "TOTAL" for f in below):
                total_row = []
                for column in table["columns"]:
                    cell = next((f for f in below if column["x"] - 3 <= f.box[0] <= column["right"] + 3
                                 or column["x"] - 3 <= f.box[0] + f.box[2] <= column["right"] + 3), None)
                    total_row.append(_literal(cell.example_text or cell.text) if cell else "")
                    if cell:
                        consumed.add(cell.field_id)
            op, asked = _table_op(table, _slug(table.get("binding") or "table", used_ids, f"p{number + 1}"),
                                  spans, heading, total_row)
            questions.extend({"page": number + 1, "question": q, "kind": "table"} for q in asked)
            if heading is not None:
                consumed.add(heading.field_id)
            for item in fields:  # the header band's words are the table's own column headers
                if (item.page == number and item.kind in ("label", "text") and item.field_id not in in_tables
                        and table["top"] - table["header_height"] - 1 <= item.y <= table["top"] + 1
                        and table["x"] - 1 <= item.box[0] <= table["x"] + table.get("frame_width", 0) + 1):
                    consumed.add(item.field_id)
            if op is not None:
                ops.append(op)
                for column in table["columns"]:
                    for field_id in column["fields"]:
                        item = by_id[field_id]
                        if item.text:
                            remove.append(item.box)
        for chart in [c for c in found["charts"] if c["page"] == number]:
            if inputs == "account":
                ops.append({"id": _slug("nav_chart", used_ids, f"p{number + 1}"), "op": "component",
                            "role": "chart", "name": "account.nav_line_chart", "box": chart["box"]})
            else:
                questions.append({"page": number + 1, "kind": "chart",
                                  "question": f"Which chart goes in the area at {chart['box']}?"})
        for item in fields:
            if item.page != number or item.field_id in consumed or item.kind in ("chart", "commentary"):
                if item.page == number and item.kind == "commentary" and item.field_id not in consumed:
                    questions.append({"page": number + 1, "kind": "commentary", "field": item.field_id,
                                      "question": "This paragraph changes from month to month. Is it commentary "
                                                  f"you write each month? It starts {item.example_text[:60]!r}."})
                continue
            if item.kind == "label":
                ops.append(_text_op(item, _slug(item.example_text or item.text, used_ids, f"p{number + 1}"),
                                    _literal(item.example_text or item.text), "label", fit=False))
                continue
            candidate = best_candidate(item)
            if candidate is None:
                questions.append({"page": number + 1, "kind": "value", "field": item.field_id,
                                  "question": f"What should {item.example_text or item.text!r} show each month"
                                              + (f" (it sits by {item.label!r})" if item.label else "") + "?"})
                continue
            name = candidate["binding"]
            role = ROLE_BY_BINDING.get(name, "title" if name == "template" else "kpi")
            ops.append(_text_op(item, _slug(name if name != "template" else (item.example_text or "title"),
                                            used_ids, f"p{number + 1}"), value_for(candidate), role, fit=True))
            if item.text:
                remove.append(item.box)
            if candidate.get("confidence") != "matched":
                questions.append({"page": number + 1, "kind": "binding", "field": item.field_id, "op": ops[-1]["id"],
                                  "question": f"Is {item.example_text or item.text!r} the "
                                              f"{bindings.CATALOGUE[name].label.lower() if name in bindings.CATALOGUE else 'value'}"
                                              f"? ({candidate['reason']})"})
        layers: list[dict[str, Any]] = []
        if remove:
            layers.append({"kind": "remove", "id": f"p{number + 1}.stale",
                           "boxes": [[round(b[0] - 1, 2), round(b[1] - 1, 2), round(b[2] + 2, 2), round(b[3] + 2, 2)]
                                     for b in remove]})
        layers.append({"kind": "overlay", "id": f"p{number + 1}.values", "ops": ops})
        pages.append({"layers": layers})
    spec = {
        "spec_version": SPEC_VERSION, "design_id": design_id, "title": title, "inputs": inputs, "base": "template",
        "template_sha256": template.sha256, "page_size": list(template.pages[0].size), "currencies": [currency],
        "owner_text": {}, "pages": pages,
        "metadata": {"/Title": "{display_name} Factsheet - {period_end|date_long}"},
        "notes": ("Drafted by the template reader from the uploaded template"
                  + (" and a completed example" if found["has_example"] else "")
                  + (", matched against the example month's snapshot" if found["matched_with_snapshot"] else "")
                  + "."),
    }
    validate_spec(spec)
    return {"spec": spec, "questions": questions}
