"""Render a factsheet from a design spec, a template and one frozen snapshot.

The engine is the only code a spec ever runs through. For each template page it
applies the spec's layers in order (remove the template's stale text, merge an
overlay drawn from bindings, run a qualified page component), then the document
components, writes the PDF, and reads it back through the generated checks
(``checks.py``) and any validators the spec names. A PDF that fails a check is
deleted before anyone can see it.

The same function renders built-in designs (``designs/*.json``), specs drafted
by the design agents, and every preview the design editor shows.
"""
from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream
from reportlab.lib.colors import HexColor
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from . import bindings, components
from .paint import IDENTITY, multiply
from .spec import SpecError, spec_sha256, validate_spec

PRODUCER = "WealthRyse spec-driven factsheet renderer"
MIN_SHRINK_RATIO = 0.7
MIN_FONT_SIZE = 3.0


class RenderError(ValueError):
    """The spec cannot render this snapshot as it stands; the message is safe to show.

    When the generated checks failed, ``report`` holds them, so the builder and the
    editor can see every failure, not only the first.
    """

    def __init__(self, message: str, report: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.report = dict(report) if report else None


@dataclass
class Drawn:
    """What the overlay drew, for the generated checks: each text with its expected origin and font."""

    texts: list[dict[str, Any]] = field(default_factory=list)
    boxes: list[dict[str, Any]] = field(default_factory=list)


def _sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _color(value: str | None, default: str = "#000000"):
    return HexColor(value or default)


# ---- text ------------------------------------------------------------------------------------

def _fitted_size(text: str, font: str, size: float, fit: Mapping[str, Any] | None) -> float:
    if not fit:
        return size
    width, minimum = float(fit["width"]), float(fit["min_size"])
    while stringWidth(text, font, size) > width and size - 0.1 >= minimum - 1e-9:
        size = round(size - 0.1, 2)
    if stringWidth(text, font, size) > width + 1e-6:
        raise RenderError(f"{text[:40]!r} does not fit its {width:g}pt space even at {minimum:g}pt")
    return size


def _origin_x(text: str, font: str, size: float, x: float, align: str) -> float:
    width = stringWidth(text, font, size)
    return x - width / 2 if align == "center" else x - width if align == "right" else x


def _draw_text(c, op: Mapping[str, Any], ctx: bindings.Context, drawn: Drawn, page_number: int) -> None:
    text = bindings.resolve(op["value"], ctx)
    if text == "":
        return
    font, align = op["font"], op.get("align", "left")
    size = _fitted_size(text, font, float(op["size"]), op.get("fit"))
    x, y = float(op["x"]), float(op["y"])
    c.setFillColor(_color(op.get("color")))
    c.setFont(font, size)
    if "rotate" in op:
        c.saveState()
        c.translate(x, y)
        c.rotate(float(op["rotate"]))
        {"center": c.drawCentredString, "right": c.drawRightString}.get(align, c.drawString)(0, 0, text)
        c.restoreState()
    else:
        {"center": c.drawCentredString, "right": c.drawRightString}.get(align, c.drawString)(x, y, text)
    if op.get("checked", True) and "rotate" not in op:
        drawn.texts.append({"id": op["id"], "page": page_number, "text": text, "font": font, "size": size,
                            "x": _origin_x(text, font, size, x, align), "y": y, "value": op["value"]})


# ---- tables ----------------------------------------------------------------------------------

def _fit_cell(value: str, font: str, size: float, available: float, overflow: str,
              max_lines: int) -> tuple[list[str], float]:
    lines = value.split("\n")
    if all(stringWidth(line, font, size) <= available for line in lines):
        return lines, size
    if overflow == "clip":
        clipped = []
        for line in lines:
            if stringWidth(line, font, size) > available:
                while line and stringWidth(line + "...", font, size) > available:
                    line = line[:-1]
                line = line.rstrip() + "..."
            clipped.append(line)
        return clipped, size
    if overflow == "wrap":
        words, wrapped, current = value.replace("\n", " ").split(), [], ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if stringWidth(candidate, font, size) <= available or not current:
                current = candidate
            else:
                wrapped.append(current)
                current = word
        wrapped.append(current)
        if len(wrapped) <= max_lines and all(stringWidth(line, font, size) <= available for line in wrapped):
            return wrapped, size
    minimum = max(MIN_FONT_SIZE, size * MIN_SHRINK_RATIO)
    shrunk = size
    while shrunk - 0.1 >= minimum - 1e-9 and any(stringWidth(line, font, shrunk) > available for line in lines):
        shrunk = round(shrunk - 0.1, 2)
    if all(stringWidth(line, font, shrunk) <= available for line in lines):
        return lines, shrunk
    raise RenderError(f"{value[:40]!r} does not fit its column, even at {minimum:.1f}pt")


def _grow_geometry(grow: Mapping[str, Any], base_top: float, header: float, preferred: float,
                   body_rows: int) -> tuple[float, float]:
    """Keep a growing table bottom-aligned and extend it upward, as the account history table does."""
    added = max(0, body_rows - int(grow["reference_rows"]))
    top = min(float(grow["max_top"]), base_top + added * preferred)
    available = top - float(grow["bottom"]) - header
    body = min(preferred, available / body_rows)
    if body < float(grow["min_body_height"]):
        raise RenderError("The table has more rows than its space allows")
    return top, body


def _draw_table(c, op: Mapping[str, Any], ctx: bindings.Context, drawn: Drawn, page_number: int) -> None:
    style = {"header_fill": "#121925", "alt_fill": "#edf1f5", "body_fill": "#ffffff", "total_fill": "#dfe3e8",
             "grid": "#cbd3dc", "text": "#121925", "header_text": "#ffffff", "line_width": 0.35,
             "font": "Helvetica", "bold_font": "Helvetica-Bold", **op.get("style", {})}
    rows = ctx.table(op["rows"])[: op.get("limit", 200)]
    columns = op["columns"]
    widths = [float(col["width"]) for col in columns]
    body = [[bindings.resolve(col["value"], ctx, row) for col in columns] for row in rows]
    raw = [[row.get(col["value"]["field"]) if isinstance(col["value"], Mapping) and "field" in col["value"]
            else None for col in columns] for row in rows]
    if "total" in op:
        body.append([bindings.resolve(cell, ctx) for cell in op["total"]])
        raw.append([None] * len(columns))
    if not body:
        raise RenderError(f"{op['id']}: the table has no rows")
    header_height, body_height = float(op["header_height"]), float(op["body_height"])
    top = float(op["top"])
    if "grow" in op:
        top, body_height = _grow_geometry(op["grow"], top, header_height, body_height, len(body))
    heading = op.get("heading")
    if heading is not None:
        heading_op = {"id": f"{op['id']}.heading", **heading}
        if "grow" in op:
            heading_op["y"] = top + float(op["grow"]["heading_gap"])
        heading_op.setdefault("x", op["x"])
        _draw_text(c, heading_op, ctx, drawn, page_number)
    padding = float(op.get("padding", 6.0))
    font_size = float(op["font_size"])
    x0 = float(op["x"])
    all_rows = [[col.get("header", "") for col in columns], *body]
    has_total = "total" in op
    y = top
    for row_index, row in enumerate(all_rows):
        height = header_height if row_index == 0 else body_height
        y -= height
        is_total = has_total and row_index == len(all_rows) - 1
        c.setFillColor(_color(
            style["header_fill"] if row_index == 0 else style["total_fill"] if is_total
            else style["alt_fill"] if row_index % 2 else style["body_fill"]))
        c.rect(x0, y, sum(widths), height, fill=1, stroke=0)
        cursor = x0
        for column_index, width in enumerate(widths):
            c.setStrokeColor(_color(style["grid"]))
            c.setLineWidth(float(style["line_width"]))
            c.rect(cursor, y, width, height, fill=0, stroke=1)
            c.setFillColor(_color(style["header_text"] if row_index == 0 else style["text"]))
            font = style["bold_font"] if row_index == 0 or is_total else style["font"]
            max_lines = max(1, int(height // (font_size * 1.05)))
            lines, size = _fit_cell(str(row[column_index]), font, font_size, width - padding,
                                    op.get("overflow", "shrink"), max_lines)
            c.setFont(font, size)
            line_height = size * 1.05
            baseline = y + height / 2 + (len(lines) - 1) * line_height / 2 - size * 0.35
            column = columns[column_index]
            align = column.get("header_align", column.get("align", "left")) if row_index == 0 else column.get("align", "left")
            for line in lines:
                if align == "right":
                    anchor = cursor + width - padding / 2
                    c.drawRightString(anchor, baseline, line)
                elif align == "center":
                    anchor = cursor + width / 2
                    c.drawCentredString(anchor, baseline, line)
                else:
                    anchor = cursor + padding / 2
                    c.drawString(anchor, baseline, line)
                if line:
                    item = {"id": f"{op['id']}.r{row_index}.c{column_index}", "page": page_number,
                            "text": line, "font": font, "size": size,
                            "x": _origin_x(line, font, size, anchor, align), "y": baseline,
                            "cell": [width - padding, line]}
                    if row_index and len(lines) == 1 and raw[row_index - 1][column_index] is not None:
                        item["value"], item["raw"] = column["value"], raw[row_index - 1][column_index]
                        item["rows"] = op["rows"]
                    drawn.texts.append(item)
                baseline -= line_height
            cursor += width
    drawn.boxes.append({"id": op["id"], "page": page_number, "box": [x0, y, sum(widths), top - y]})


# ---- removal ---------------------------------------------------------------------------------

def remove_content(page: Any, boxes: list[list[float]], *, images: bool) -> dict[str, int]:
    """Delete the template's own text whose origin lies in a box (and images wholly inside one).

    Covers every text-showing operator (Tj, TJ, ' and "), and walks nested forms,
    so a stale value is removed from the page, not hidden under a white box.
    """
    regions = [tuple(map(float, box)) for box in boxes]
    counts = {"text": 0, "images": 0}

    def inside(x: float, y: float) -> bool:
        return any(a <= x <= a + w and b <= y <= b + h for a, b, w, h in regions)

    def walk(stream: Any, resources: Any, transform: tuple[float, ...], depth: int = 0) -> None:
        if depth > 20:
            raise RenderError("The template's forms nest too deeply")
        content = ContentStream(stream, page.pdf)
        cm, tm, tlm, leading, stack, operations = transform, IDENTITY, IDENTITY, 0.0, [], []
        for args, op in content.operations:
            if op == b"q":
                stack.append((cm, tm, tlm, leading))
            elif op == b"Q":
                cm, tm, tlm, leading = stack.pop()
            elif op == b"cm":
                cm = multiply(tuple(map(float, args)), cm)
            elif op == b"BT":
                tm = tlm = IDENTITY
            elif op == b"Tm":
                tm = tlm = tuple(map(float, args))
            elif op == b"TL":
                leading = float(args[0])
            elif op in (b"Td", b"TD"):
                if op == b"TD":
                    leading = -float(args[1])
                tm = tlm = multiply((1.0, 0.0, 0.0, 1.0, float(args[0]), float(args[1])), tlm)
            elif op == b"T*":
                tm = tlm = multiply((1.0, 0.0, 0.0, 1.0, 0.0, -leading), tlm)
            elif op in (b"Tj", b"TJ", b"'", b'"'):
                if op in (b"'", b'"'):
                    tm = tlm = multiply((1.0, 0.0, 0.0, 1.0, 0.0, -leading), tlm)
                matrix = multiply(tm, cm)
                if inside(matrix[4], matrix[5]):
                    counts["text"] += 1
                    if op in (b"'", b'"'):  # keep the line advance, drop the glyphs
                        operations.append(([], b"T*"))
                    continue
            elif op == b"Do":
                child = resources.get("/XObject", {}).get(args[0]) if resources else None
                if child is not None:
                    child = child.get_object()
                    if child.get("/Subtype") == "/Image" and images:
                        corners = [(cm[4] + x * cm[0] + y * cm[2], cm[5] + x * cm[1] + y * cm[3])
                                   for x, y in ((0, 0), (0, 1), (1, 0), (1, 1))]
                        if all(inside(x, y) for x, y in corners):
                            counts["images"] += 1
                            continue
                    elif child.get("/Subtype") == "/Form":
                        walk(child, child.get("/Resources", resources),
                             multiply(tuple(map(float, child.get("/Matrix", IDENTITY))), cm), depth + 1)
            operations.append((args, op))
        content.operations = operations
        if depth == 0:
            page.replace_contents(content)
        else:
            stream.set_data(content.get_data())

    walk(page.get_contents(), page["/Resources"], IDENTITY)
    return counts


# ---- render ----------------------------------------------------------------------------------

def _overlay(spec_layer: Mapping[str, Any], ctx: bindings.Context, size: tuple[float, float], drawn: Drawn,
             page_number: int):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=size)
    for op in spec_layer["ops"]:
        kind = op["op"]
        if kind == "fill":
            c.setFillColor(_color(op.get("color"), "#ffffff"))
            if "stroke" in op:
                c.setStrokeColor(_color(op["stroke"]))
                c.setLineWidth(float(op.get("line_width", 1.0)))
            c.rect(*op["box"], fill=1, stroke=1 if "stroke" in op else 0)
            if op.get("role") != "mask":
                drawn.boxes.append({"id": op["id"], "page": page_number, "box": list(op["box"])})
        elif kind == "line":
            c.setStrokeColor(_color(op.get("color")))
            c.setLineWidth(float(op.get("width", 1.0)))
            if op.get("dash"):
                c.setDash(*op["dash"])
            c.line(*op["from"], *op["to"])
            if op.get("dash"):
                c.setDash()
        elif kind == "text":
            _draw_text(c, op, ctx, drawn, page_number)
        elif kind == "table":
            _draw_table(c, op, ctx, drawn, page_number)
        elif kind == "component":
            components.REGISTRY[op["name"]].fn(c, ctx, op.get("params", {}), op.get("box"))
            if op.get("box"):
                drawn.boxes.append({"id": op["id"], "page": page_number, "box": list(op["box"]),
                                    "component": op["name"]})
    c.save()
    buffer.seek(0)
    return PdfReader(buffer).pages[0]


def render(spec: Mapping[str, Any], snapshot: Mapping[str, Any], *, template_path: str | Path,
           output_path: str | Path, example_path: str | Path | None = None,
           run_checks: bool = True) -> dict[str, Any]:
    """Render, then read back and check. Returns the result record; raises and leaves no file on failure."""
    from . import checks
    validate_spec(spec)
    paths = {"template": Path(template_path), "example": Path(example_path) if example_path else None}
    if spec.get("template_sha256") and _sha256(paths["template"]) != spec["template_sha256"]:
        raise RenderError("This is not the template the design was measured from")
    if spec.get("example_sha256"):
        if paths["example"] is None:
            raise RenderError("This design needs its completed example")
        if _sha256(paths["example"]) != spec["example_sha256"]:
            raise RenderError("This is not the completed example the design was measured from")
    destination = Path(output_path)
    if destination.resolve() in {p.resolve() for p in paths.values() if p is not None}:
        raise RenderError("The output must be a new file, not a template")
    currencies = tuple(spec["currencies"])
    if snapshot.get("reporting_currency") not in currencies:
        raise RenderError(f"This design reports in {', '.join(currencies)}, not {snapshot.get('reporting_currency')}")
    owner = {key: entry["value"] for key, entry in spec.get("owner_text", {}).items()}
    try:
        ctx = bindings.build_context(snapshot, owner_text=owner,
                                     inputs_builder=components.inputs_builder(spec["inputs"], currencies))
    except bindings.BindingError as error:
        raise RenderError(str(error)) from error
    state: dict[str, Any] = {"paths": paths}
    source_results: dict[str, Any] = {}
    for step in spec.get("source_checks", []):
        source_results.update(components.REGISTRY[step["name"]].fn(paths, ctx, step.get("params", {}), state) or {})

    base = paths["example"] if spec.get("base") == "completed_example" else paths["template"]
    reader = PdfReader(str(base))
    if len(reader.pages) != len(spec["pages"]):
        raise RenderError(f"The template has {len(reader.pages)} pages; the design has {len(spec['pages'])}")
    writer = PdfWriter(clone_from=reader)
    drawn = Drawn()
    removed = []
    for page_number, (page, page_spec) in enumerate(zip(writer.pages, spec["pages"], strict=True)):
        size = (float(page.mediabox.width), float(page.mediabox.height))
        if abs(size[0] - spec["page_size"][0]) > 1.0 or abs(size[1] - spec["page_size"][1]) > 1.0:
            raise RenderError("The template's page size differs from the design's")
        state["page_number"] = page_number
        page_removed = {"text": 0, "images": 0}
        for layer in page_spec["layers"]:
            if layer["kind"] == "remove":
                counts = remove_content(page, layer["boxes"], images=bool(layer.get("images")))
                page_removed = {k: page_removed[k] + counts[k] for k in page_removed}
            elif layer["kind"] == "overlay":
                if not layer["ops"]:  # an empty canvas saves no page, so there is nothing to merge
                    continue
                page.merge_page(_overlay(layer, ctx, size, drawn, page_number))
            else:
                components.REGISTRY[layer["name"]].fn(page, ctx, layer.get("params", {}), state)
        removed.append(page_removed)
    metadata: dict[str, str] = {}
    for step in spec.get("post", []):
        metadata.update(components.REGISTRY[step["name"]].fn(writer, ctx, step.get("params", {}), state) or {})
    sha = spec_sha256(spec)
    document_metadata = {"/Producer": PRODUCER, "/DesignID": spec["design_id"], "/DesignSpecSHA256": sha}
    for key, value in spec.get("metadata", {}).items():
        document_metadata[key] = bindings.resolve(value, ctx)
    document_metadata.update(metadata)
    writer.add_metadata(document_metadata)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as stream:
        writer.write(stream)
    report: dict[str, Any] = {}
    if run_checks:
        try:
            report = checks.run(destination, spec, ctx, drawn)
            if report["status"] == "fail":
                failed = [c for c in report["checks"] if c["status"] == "fail"]
                raise RenderError(failed[0]["detail"] if failed else "The rendered factsheet failed its checks",
                                  report)
            for step in spec.get("validators", []):
                components.REGISTRY[step["name"]].fn(destination, ctx, step.get("params", {}), state)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
    result = {
        "output_path": str(destination), "pages": len(spec["pages"]), "design_id": spec["design_id"],
        "design_spec_sha256": sha, "layout_version": f"spec:{spec['design_id']}:{sha[:12]}",
        "layout_family": spec["design_id"], "period_end": ctx.scalars["period_end"],
        "data_as_of": ctx.scalars["data_as_of"], "reporting_currency": ctx.scalars["reporting_currency"],
        "template_sha256": _sha256(paths["template"]),
        "variable_content_removed": removed, "checks": report, **source_results,
    }
    if paths["example"] is not None:
        result["completed_example_sha256"] = _sha256(paths["example"])
    if "pdf_accuracy_validation" in state:
        result["pdf_accuracy_validation"] = state["pdf_accuracy_validation"]
    result["drawn"] = {"texts": drawn.texts, "boxes": drawn.boxes}
    return result


def render_preview(spec: Mapping[str, Any], snapshot: Mapping[str, Any], *, template_path: str | Path,
                   example_path: str | Path | None = None) -> tuple[bytes, dict[str, Any]]:
    """Render to memory for the review page and the editor: the PDF bytes and the result, checks included.

    A preview that fails its checks still returns the result (with ``error``) so
    the agents and the owner can see why; it never returns the failing PDF.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder) / "preview.pdf"
        try:
            result = render(spec, snapshot, template_path=template_path, output_path=target,
                            example_path=example_path)
        except (RenderError, SpecError, bindings.BindingError, components.ComponentError, ValueError) as error:
            failed = {"error": str(error)}
            if getattr(error, "report", None):
                failed["checks"] = error.report
            return b"", failed
        return target.read_bytes(), result
