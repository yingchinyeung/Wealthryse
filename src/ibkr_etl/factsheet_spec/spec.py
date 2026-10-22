"""The design spec: a closed, checkable description of one factsheet design.

A spec is data. It names the template it was measured from (by SHA-256), the
inputs it reads, and for each page an ordered list of layers:

- ``remove``: strip the template's own text (and, optionally, images) inside boxes,
  through nested forms, so stale values are gone rather than painted over;
- ``overlay``: draw operations on a fresh canvas merged over the page;
- ``component``: run one of the qualified code components (``components.py``),
  such as the Alpha World return distribution or the native-font text layer.

Overlay operations are ``fill``, ``line``, ``text``, ``table`` and ``component``.
Every printed value is a binding from ``bindings.CATALOGUE`` or an entry in the
spec's own ``owner_text`` (the About paragraph, a benchmark name, allocation
labels): text the owner wrote and approved, never a figure.

``validate_spec`` rejects anything outside that vocabulary before a spec is
stored, rendered, edited or shown to anyone, and ``spec_sha256`` is the identity a
qualified design version is bound to.
"""
from __future__ import annotations

import hashlib
import json
import re
from importlib import resources
from typing import Any, Mapping

from reportlab.pdfbase.pdfmetrics import standardFonts

from . import bindings

SPEC_VERSION = 1
MAX_SPEC_BYTES = 512 * 1024
MAX_OPS_PER_LAYER = 400
ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
INPUTS = ("account", "alpha_world_ms", "alpha_world_t", "us_istrategy")
BASES = ("template", "completed_example")
ALIGNS = ("left", "center", "right")
OVERFLOW = ("shrink", "wrap", "clip")
# Standard PDF fonts every renderer has; embedded template fonts are reached
# through components, which measure them from the template itself.
FONTS = frozenset(standardFonts)


class SpecError(ValueError):
    """The spec is not valid; the message says where and is safe to show."""


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def spec_sha256(spec: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical(spec).encode("utf-8")).hexdigest()


def load_design(design_id: str) -> dict[str, Any]:
    """A built-in design spec shipped with the platform (``designs/<id>.json``)."""
    if not ID.fullmatch(design_id):
        raise SpecError("Unknown design")
    try:
        text = resources.files(__package__).joinpath("designs", f"{design_id}.json").read_text("utf-8")
    except FileNotFoundError as error:
        raise SpecError(f"Unknown design: {design_id}") from error
    spec = json.loads(text)
    validate_spec(spec)
    return spec


def built_in_designs() -> list[str]:
    folder = resources.files(__package__).joinpath("designs")
    return sorted(item.name[:-5] for item in folder.iterdir() if item.name.endswith(".json"))


def _box(value: Any, where: str) -> None:
    if (not isinstance(value, list) or len(value) != 4
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)):
        raise SpecError(f"{where}: a box is [x, y, width, height]")
    if value[2] < 0 or value[3] < 0:
        raise SpecError(f"{where}: a box cannot have negative size")


def _number(value: Any, where: str, *, low: float = -10000.0, high: float = 10000.0) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not low <= value <= high:
        raise SpecError(f"{where}: expected a number between {low:g} and {high:g}")


def _color(value: Any, where: str) -> None:
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise SpecError(f"{where}: colours are #rrggbb")


def _font(value: Any, where: str) -> None:
    if value not in FONTS:
        raise SpecError(f"{where}: unknown font {value!r}")


def _value(value: Any, where: str, *, owner: Mapping[str, Any], row_fields: tuple[str, ...] = ()) -> None:
    """A printable value: template text, a binding, a column, or a choice between them."""
    if isinstance(value, str):
        for name, pipe in bindings.template_names(value):
            if name not in row_fields and name not in owner and (
                    name not in bindings.CATALOGUE or bindings.CATALOGUE[name].kind != "scalar"):
                raise SpecError(f"{where}: unknown value {{{name}}}")
            if pipe is not None and pipe not in bindings._PIPES:
                raise SpecError(f"{where}: unknown format |{pipe}")
        return
    if not isinstance(value, Mapping):
        raise SpecError(f"{where}: a value is text, a binding or a choice")
    if "choose" in value:
        if set(value) != {"choose"} or not isinstance(value["choose"], list) or not value["choose"]:
            raise SpecError(f"{where}: a choice lists its options")
        for index, option in enumerate(value["choose"]):
            if not isinstance(option, Mapping) or not set(option) <= {"when", "value"} or "value" not in option:
                raise SpecError(f"{where}.choose[{index}]: an option is {{when, value}}")
            if "when" in option:
                name = bindings.condition_name(str(option["when"]))
                if name not in bindings.CATALOGUE or bindings.CATALOGUE[name].kind != "scalar":
                    raise SpecError(f"{where}.choose[{index}]: unknown condition {option['when']!r}")
            _value(option["value"], f"{where}.choose[{index}]", owner=owner, row_fields=row_fields)
        return
    if "bind" in value or "field" in value:
        key = "bind" if "bind" in value else "field"
        if not set(value) <= {key, "format"}:
            raise SpecError(f"{where}: a binding is {{{key}, format}}")
        name = value[key]
        if key == "bind" and name not in owner and (
                name not in bindings.CATALOGUE or bindings.CATALOGUE[name].kind != "scalar"):
            raise SpecError(f"{where}: unknown value {name!r}")
        if key == "field" and name not in row_fields:
            raise SpecError(f"{where}: unknown column {name!r}")
        fmt = value.get("format")
        if fmt is not None:
            if not isinstance(fmt, Mapping) or fmt.get("kind") not in bindings.FORMAT_KINDS:
                raise SpecError(f"{where}: unknown format")
            allowed = {"kind", "digits", "signed", "suffix", "prefix", "none", "abs", "negate_abs", "thousands",
                       "style"}
            if not set(fmt) <= allowed:
                raise SpecError(f"{where}: unknown format option {sorted(set(fmt) - allowed)[0]}")
        return
    raise SpecError(f"{where}: a value is text, a binding or a choice")


def _component(op: Mapping[str, Any], where: str, layer: str) -> None:
    from . import components
    name = op.get("name")
    entry = components.REGISTRY.get(name)
    if entry is None:
        raise SpecError(f"{where}: unknown component {name!r}")
    if entry.layer != layer:
        raise SpecError(f"{where}: {name} is a {entry.layer} component")
    params = op.get("params", {})
    if not isinstance(params, Mapping) or not set(params) <= set(entry.params):
        extra = sorted(set(params) - set(entry.params)) if isinstance(params, Mapping) else ["params"]
        raise SpecError(f"{where}: {name} does not take {extra[0]!r}")
    if "box" in op:
        _box(op["box"], where)


def _text_op(op: Mapping[str, Any], where: str, owner: Mapping[str, Any]) -> None:
    allowed = {"id", "op", "x", "y", "align", "font", "size", "color", "value", "fit", "rotate", "checked", "role"}
    if not set(op) <= allowed:
        raise SpecError(f"{where}: unknown text option {sorted(set(op) - allowed)[0]}")
    for key in ("x", "y"):
        _number(op.get(key), f"{where}.{key}")
    _number(op.get("size"), f"{where}.size", low=1.0, high=72.0)
    _font(op.get("font"), f"{where}.font")
    if op.get("align", "left") not in ALIGNS:
        raise SpecError(f"{where}: align is left, center or right")
    _color(op.get("color", "#000000"), f"{where}.color")
    _value(op.get("value"), f"{where}.value", owner=owner)
    if "fit" in op:
        fit = op["fit"]
        if not isinstance(fit, Mapping) or set(fit) != {"width", "min_size"}:
            raise SpecError(f"{where}.fit: {{width, min_size}}")
        _number(fit["width"], f"{where}.fit.width", low=1.0)
        _number(fit["min_size"], f"{where}.fit.min_size", low=1.0, high=72.0)
    if "rotate" in op:
        _number(op["rotate"], f"{where}.rotate", low=-360.0, high=360.0)


def _table_op(op: Mapping[str, Any], where: str, owner: Mapping[str, Any]) -> None:
    allowed = {"id", "op", "x", "top", "rows", "limit", "columns", "header_height", "body_height", "font_size",
               "padding", "total", "overflow", "grow", "style", "heading", "role"}
    if not set(op) <= allowed:
        raise SpecError(f"{where}: unknown table option {sorted(set(op) - allowed)[0]}")
    binding = bindings.CATALOGUE.get(op.get("rows"))
    if binding is None or binding.kind != "rows":
        raise SpecError(f"{where}: unknown table {op.get('rows')!r}")
    _number(op.get("x"), f"{where}.x")
    _number(op.get("top"), f"{where}.top")
    for key in ("header_height", "body_height"):
        _number(op.get(key), f"{where}.{key}", low=1.0, high=200.0)
    _number(op.get("font_size"), f"{where}.font_size", low=1.0, high=36.0)
    if "padding" in op:
        _number(op["padding"], f"{where}.padding", low=0.0, high=40.0)
    if "limit" in op and (not isinstance(op["limit"], int) or not 1 <= op["limit"] <= 200):
        raise SpecError(f"{where}.limit: 1 to 200 rows")
    if op.get("overflow", "shrink") not in OVERFLOW:
        raise SpecError(f"{where}.overflow: shrink, wrap or clip")
    columns = op.get("columns")
    if not isinstance(columns, list) or not 1 <= len(columns) <= 12:
        raise SpecError(f"{where}: a table has 1 to 12 columns")
    for index, column in enumerate(columns):
        cw = f"{where}.columns[{index}]"
        if not isinstance(column, Mapping) or not set(column) <= {"width", "align", "header_align", "header", "value"}:
            raise SpecError(f"{cw}: a column is {{width, align, header_align, header, value}}")
        _number(column.get("width"), f"{cw}.width", low=1.0, high=1000.0)
        for key in ("align", "header_align"):
            if column.get(key, "left") not in ALIGNS:
                raise SpecError(f"{cw}.{key}: left, center or right")
        if not isinstance(column.get("header", ""), str):
            raise SpecError(f"{cw}.header: text")
        _value(column.get("value"), f"{cw}.value", owner=owner, row_fields=binding.fields)
    if "total" in op:
        total = op["total"]
        if not isinstance(total, list) or len(total) != len(columns):
            raise SpecError(f"{where}.total: one cell per column")
        for index, cell in enumerate(total):
            _value(cell, f"{where}.total[{index}]", owner=owner)
    if "grow" in op:
        grow = op["grow"]
        keys = {"reference_rows", "max_top", "bottom", "min_body_height", "heading_gap"}
        if not isinstance(grow, Mapping) or set(grow) != keys:
            raise SpecError(f"{where}.grow: {{{', '.join(sorted(keys))}}}")
        for key in keys:
            _number(grow[key], f"{where}.grow.{key}")
    if "heading" in op:
        _text_op({"op": "text", "x": 0, "y": 0, **op["heading"]}, f"{where}.heading", owner)
    style = op.get("style", {})
    style_keys = {"header_fill", "alt_fill", "body_fill", "total_fill", "grid", "text", "header_text", "line_width",
                  "font", "bold_font"}
    if not isinstance(style, Mapping) or not set(style) <= style_keys:
        raise SpecError(f"{where}.style: unknown option")
    for key in ("header_fill", "alt_fill", "body_fill", "total_fill", "grid", "text", "header_text"):
        if key in style:
            _color(style[key], f"{where}.style.{key}")
    for key in ("font", "bold_font"):
        if key in style:
            _font(style[key], f"{where}.style.{key}")


def validate_spec(spec: Any) -> None:
    """Raise SpecError unless the spec uses only the closed vocabulary."""
    if not isinstance(spec, Mapping):
        raise SpecError("A spec is a JSON object")
    if len(canonical(spec).encode("utf-8")) > MAX_SPEC_BYTES:
        raise SpecError("The spec is larger than 512 KiB")
    allowed = {"spec_version", "design_id", "title", "inputs", "base", "template_sha256", "example_sha256",
               "page_size", "currencies", "owner_text", "pages", "post", "metadata", "validators", "notes",
               "source_checks"}
    if not set(spec) <= allowed:
        raise SpecError(f"Unknown spec field: {sorted(set(spec) - allowed)[0]}")
    if spec.get("spec_version") != SPEC_VERSION:
        raise SpecError("Unsupported spec version")
    if not isinstance(spec.get("design_id"), str) or not ID.fullmatch(spec["design_id"]):
        raise SpecError("design_id: lower-case letters, digits, dot, dash or underscore")
    if spec.get("inputs") not in INPUTS:
        raise SpecError(f"inputs: one of {', '.join(INPUTS)}")
    if spec.get("base", "template") not in BASES:
        raise SpecError("base: template or completed_example")
    for key in ("template_sha256", "example_sha256"):
        if key in spec and spec[key] is not None and not re.fullmatch(r"[0-9a-f]{64}", str(spec[key])):
            raise SpecError(f"{key}: a SHA-256")
    if spec.get("base") == "completed_example" and not spec.get("example_sha256"):
        raise SpecError("A design drawn over its completed example must pin the example's SHA-256")
    size = spec.get("page_size")
    if not isinstance(size, list) or len(size) != 2:
        raise SpecError("page_size: [width, height]")
    for value in size:
        _number(value, "page_size", low=72.0, high=5000.0)
    currencies = spec.get("currencies")
    if not isinstance(currencies, list) or not currencies or not all(
            isinstance(c, str) and re.fullmatch(r"[A-Z]{3}", c) for c in currencies):
        raise SpecError("currencies: ISO codes the design may report in")
    owner = spec.get("owner_text", {})
    if not isinstance(owner, Mapping):
        raise SpecError("owner_text: an object")
    for key, entry in owner.items():
        if not ID.fullmatch(key) or key in bindings.CATALOGUE:
            raise SpecError(f"owner_text.{key}: a new lower-case name")
        if not isinstance(entry, Mapping) or not set(entry) <= {"label", "value", "guarded"} or not isinstance(
                entry.get("value"), str) or not isinstance(entry.get("label", ""), str):
            raise SpecError(f"owner_text.{key}: {{label, value, guarded}}")
        if len(entry["value"]) > 4000:
            raise SpecError(f"owner_text.{key}: at most 4,000 characters")
    pages = spec.get("pages")
    if not isinstance(pages, list) or not 1 <= len(pages) <= 8:
        raise SpecError("pages: 1 to 8 pages")
    ids: set[str] = set()
    for page_index, page in enumerate(pages):
        if not isinstance(page, Mapping) or set(page) != {"layers"} or not isinstance(page["layers"], list):
            raise SpecError(f"pages[{page_index}]: {{layers: [...]}}")
        for layer_index, layer in enumerate(page["layers"]):
            where = f"pages[{page_index}].layers[{layer_index}]"
            kind = layer.get("kind") if isinstance(layer, Mapping) else None
            if kind == "remove":
                if not set(layer) <= {"kind", "boxes", "images", "id"}:
                    raise SpecError(f"{where}: a remove layer is {{boxes, images}}")
                if not isinstance(layer.get("boxes"), list) or not layer["boxes"]:
                    raise SpecError(f"{where}: list the boxes to clear")
                for index, box in enumerate(layer["boxes"]):
                    _box(box, f"{where}.boxes[{index}]")
            elif kind == "overlay":
                if not set(layer) <= {"kind", "ops", "id"} or not isinstance(layer.get("ops"), list):
                    raise SpecError(f"{where}: an overlay layer is {{ops}}")
                if len(layer["ops"]) > MAX_OPS_PER_LAYER:
                    raise SpecError(f"{where}: at most {MAX_OPS_PER_LAYER} operations")
                for op_index, op in enumerate(layer["ops"]):
                    ow = f"{where}.ops[{op_index}]"
                    if not isinstance(op, Mapping):
                        raise SpecError(f"{ow}: an operation is an object")
                    op_id = op.get("id")
                    if not isinstance(op_id, str) or not ID.fullmatch(op_id) or op_id in ids:
                        raise SpecError(f"{ow}: every operation has a unique id")
                    ids.add(op_id)
                    kind_op = op.get("op")
                    if kind_op == "fill":
                        if not set(op) <= {"id", "op", "box", "color", "stroke", "line_width", "role"}:
                            raise SpecError(f"{ow}: a fill is {{box, color, stroke, line_width}}")
                        _box(op.get("box"), ow)
                        _color(op.get("color", "#ffffff"), f"{ow}.color")
                        if "stroke" in op:
                            _color(op["stroke"], f"{ow}.stroke")
                    elif kind_op == "line":
                        if not set(op) <= {"id", "op", "from", "to", "color", "width", "dash", "role"}:
                            raise SpecError(f"{ow}: a line is {{from, to, color, width, dash}}")
                        for key in ("from", "to"):
                            point = op.get(key)
                            if not isinstance(point, list) or len(point) != 2:
                                raise SpecError(f"{ow}.{key}: [x, y]")
                            for value in point:
                                _number(value, f"{ow}.{key}")
                        _color(op.get("color", "#000000"), f"{ow}.color")
                        _number(op.get("width", 1.0), f"{ow}.width", low=0.0, high=20.0)
                    elif kind_op == "text":
                        _text_op(op, ow, owner)
                    elif kind_op == "table":
                        _table_op(op, ow, owner)
                    elif kind_op == "component":
                        if not set(op) <= {"id", "op", "name", "params", "box", "role"}:
                            raise SpecError(f"{ow}: a component is {{name, params, box}}")
                        _component(op, ow, "overlay")
                    else:
                        raise SpecError(f"{ow}: unknown operation {kind_op!r}")
            elif kind == "component":
                if not set(layer) <= {"kind", "name", "params", "id"}:
                    raise SpecError(f"{where}: a component layer is {{name, params}}")
                _component(layer, where, "page")
            else:
                raise SpecError(f"{where}: kind is remove, overlay or component")
    for index, step in enumerate(spec.get("post", [])):
        if not isinstance(step, Mapping) or not set(step) <= {"name", "params"}:
            raise SpecError(f"post[{index}]: {{name, params}}")
        _component(step, f"post[{index}]", "document")
    for index, step in enumerate(spec.get("source_checks", [])):
        if not isinstance(step, Mapping) or not set(step) <= {"name", "params"}:
            raise SpecError(f"source_checks[{index}]: {{name, params}}")
        _component(step, f"source_checks[{index}]", "source")
    for index, step in enumerate(spec.get("validators", [])):
        if not isinstance(step, Mapping) or not set(step) <= {"name", "params"}:
            raise SpecError(f"validators[{index}]: {{name, params}}")
        _component(step, f"validators[{index}]", "validator")
    metadata = spec.get("metadata", {})
    if not isinstance(metadata, Mapping) or not all(
            isinstance(k, str) and re.fullmatch(r"/[A-Za-z][A-Za-z0-9]{0,40}", k) for k in metadata):
        raise SpecError("metadata: PDF keys like /Title")
    for key, value in metadata.items():
        _value(value, f"metadata.{key}", owner=owner)


def op_index(spec: Mapping[str, Any]) -> dict[str, tuple[int, int, int]]:
    """Every overlay operation's (page, layer, position) by id."""
    found = {}
    for p, page in enumerate(spec["pages"]):
        for l, layer in enumerate(page["layers"]):
            for o, op in enumerate(layer.get("ops", []) if layer.get("kind") == "overlay" else []):
                found[op["id"]] = (p, l, o)
    return found
