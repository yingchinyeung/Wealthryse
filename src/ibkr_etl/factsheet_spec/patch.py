"""Spec edits: the only way an agent (builder or editor) can change a design.

An edit is one of a few closed operations on a spec. Each names what it changes
by the operation's id, so an edit can be checked, described and undone:

- ``set``: change one property of an operation (``x``, ``size``, ``font``, ``color``,
  ``value``, a table's ``font_size`` ...);
- ``move``: shift an operation by ``dx``/``dy`` points;
- ``set_column``: change one property of one table column;
- ``add``: add a new operation to a page's overlay;
- ``delete``: remove an operation;
- ``set_text``: change an owner-text entry (the About paragraph, a benchmark name);
- ``add_box`` / ``delete_box``: change what is cleared from the template.

``apply`` validates the result with ``validate_spec`` and describes every change
in plain words written by code, so the summary a person approves is not the
model's own account of what it did. Guarded areas (owner text marked guarded,
operations with the ``disclaimer`` or ``benchmark`` role, and re-pointing a figure
at a different metric) are refused unless the person confirmed them.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from . import bindings
from .spec import SpecError, op_index, validate_spec

OPS = ("set", "move", "set_column", "add", "delete", "set_text", "add_box", "delete_box")
SETTABLE = {
    "text": {"x", "y", "align", "font", "size", "color", "value", "fit", "rotate", "checked"},
    "table": {"x", "top", "limit", "header_height", "body_height", "font_size", "padding", "total", "overflow",
              "grow", "style", "heading"},
    "fill": {"box", "color", "stroke", "line_width"},
    "line": {"from", "to", "color", "width", "dash"},
    "component": {"box", "params"},
}
COLUMN_SETTABLE = {"width", "align", "header_align", "header", "value"}
GUARDED_ROLES = {"disclaimer", "benchmark"}
MAX_EDITS = 60


class PatchError(ValueError):
    """An edit cannot be applied; the message says which and why, and is safe to show."""


@dataclass
class Applied:
    spec: dict[str, Any]
    changes: list[str] = field(default_factory=list)
    guarded: list[str] = field(default_factory=list)


def _metric(value: Any) -> set[str]:
    """The catalogue figures a value prints."""
    if isinstance(value, str):
        return {name for name, _ in bindings.template_names(value) if name in bindings.CATALOGUE}
    if isinstance(value, Mapping):
        if "bind" in value:
            return {str(value["bind"])}
        if "field" in value:
            return {"field:" + str(value["field"])}
        if "choose" in value:
            return set().union(*(_metric(option.get("value")) for option in value["choose"]))
    return set()


def _show(value: Any) -> str:
    if isinstance(value, Mapping) and "bind" in value:
        name = value["bind"]
        label = bindings.CATALOGUE[name].label.lower() if name in bindings.CATALOGUE else name
        return f"the {label}"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)[:80]
    return repr(value)[:80]


def _op_label(op: Mapping[str, Any]) -> str:
    if op.get("op") == "text":
        value = op.get("value")
        return f"the {_show(value)[4:] if _show(value).startswith('the ') else _show(value)} text"
    if op.get("op") == "table":
        binding = bindings.CATALOGUE.get(op.get("rows", ""))
        return f"the {binding.label.lower() if binding else op.get('rows')} table"
    if op.get("op") == "component":
        return f"the {op.get('name')} chart"
    return f"the {op.get('op')} {op['id']}"


def _decode(raw: Any) -> Any:
    """Agent edits carry values as JSON text so their schema can stay strict."""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw


def normalize(edit: Mapping[str, Any]) -> dict[str, Any]:
    """An edit from an agent's strict JSON (all keys present, value as JSON text) as a plain edit."""
    kind = edit.get("op")
    if kind not in OPS:
        raise PatchError(f"Unknown edit {kind!r}")
    out: dict[str, Any] = {"op": kind}
    for key in ("target", "property", "key"):
        if edit.get(key) not in (None, ""):
            out[key] = str(edit[key])
    for key in ("dx", "dy"):
        if edit.get(key) not in (None, "") and float(edit[key]) != 0.0:
            out[key] = float(edit[key])
    if edit.get("page") not in (None, "") and int(edit["page"]) >= 1:
        out["page"] = int(edit["page"])
    if edit.get("column") not in (None, "") and int(edit["column"]) >= 0:
        out["column"] = int(edit["column"])
    if "value_json" in edit and edit["value_json"] not in (None, ""):
        out["value"] = _decode(edit["value_json"])
    elif "value" in edit:
        out["value"] = edit["value"]
    return out


def _locate(spec: dict[str, Any], target: str) -> dict[str, Any]:
    index = op_index(spec)
    if target not in index:
        raise PatchError(f"There is no operation {target!r} in this design")
    p, l, o = index[target]
    return spec["pages"][p]["layers"][l]["ops"][o]


def _round(value: Any) -> Any:
    return round(value, 2) if isinstance(value, float) else value


def apply(spec: Mapping[str, Any], edits: Sequence[Mapping[str, Any]], *, allow_guarded: bool = False) -> Applied:
    """The spec with the edits applied, validated, with a plain sentence per change."""
    if len(edits) > MAX_EDITS:
        raise PatchError(f"At most {MAX_EDITS} edits at once")
    result = copy.deepcopy(dict(spec))
    applied = Applied(result)
    for number, raw in enumerate(edits, 1):
        edit = normalize(raw) if "value_json" in raw else dict(raw)
        kind = edit.get("op")
        where = f"Edit {number}"
        try:
            if kind == "set":
                op = _locate(result, edit["target"])
                prop = edit["property"]
                if prop not in SETTABLE.get(op["op"], set()):
                    raise PatchError(f"{where}: {prop!r} cannot be changed on a {op['op']}")
                old = op.get(prop)
                new = edit["value"]
                if op.get("role") in GUARDED_ROLES:
                    applied.guarded.append(f"{_op_label(op)} is a {op['role']} area")
                if prop == "value" and _metric(old) and _metric(new) != _metric(old):
                    applied.guarded.append(f"{_op_label(op)} would show a different figure ({_show(new)})")
                op[prop] = new
                applied.changes.append(f"Changed {_op_label(op)}'s {prop} from {_show(old)} to {_show(new)}.")
            elif kind == "move":
                op = _locate(result, edit["target"])
                dx, dy = float(edit.get("dx", 0.0)), float(edit.get("dy", 0.0))
                if abs(dx) > 600 or abs(dy) > 900:
                    raise PatchError(f"{where}: a move is at most a page")
                if "box" in op:
                    op["box"] = [_round(op["box"][0] + dx), _round(op["box"][1] + dy), op["box"][2], op["box"][3]]
                elif op["op"] == "line":
                    op["from"] = [_round(op["from"][0] + dx), _round(op["from"][1] + dy)]
                    op["to"] = [_round(op["to"][0] + dx), _round(op["to"][1] + dy)]
                else:
                    op["x"] = _round(float(op["x"]) + dx)
                    key = "top" if op["op"] == "table" else "y"
                    op[key] = _round(float(op[key]) + dy)
                    if op["op"] == "table" and "heading" in op:
                        op["heading"]["y"] = _round(float(op["heading"]["y"]) + dy)
                        if "x" in op["heading"]:
                            op["heading"]["x"] = _round(float(op["heading"]["x"]) + dx)
                if op.get("role") in GUARDED_ROLES:
                    applied.guarded.append(f"{_op_label(op)} is a {op['role']} area")
                parts = []
                if dx:
                    parts.append(f"{abs(dx):g}pt {'right' if dx > 0 else 'left'}")
                if dy:
                    parts.append(f"{abs(dy):g}pt {'up' if dy > 0 else 'down'}")
                applied.changes.append(f"Moved {_op_label(op)} {' and '.join(parts) or 'nowhere'}.")
            elif kind == "set_column":
                op = _locate(result, edit["target"])
                if op["op"] != "table":
                    raise PatchError(f"{where}: {edit['target']} is not a table")
                column = int(edit["column"])
                if not 0 <= column < len(op["columns"]):
                    raise PatchError(f"{where}: the table has {len(op['columns'])} columns")
                prop = edit["property"]
                if prop not in COLUMN_SETTABLE:
                    raise PatchError(f"{where}: a column's {prop!r} cannot be changed")
                old = op["columns"][column].get(prop)
                if prop == "value" and _metric(old) != _metric(edit["value"]):
                    applied.guarded.append(f"column {column + 1} of {_op_label(op)} would show a different figure")
                op["columns"][column][prop] = edit["value"]
                header = op["columns"][column].get("header") or f"column {column + 1}"
                applied.changes.append(f"Changed the {header.splitlines()[0]!r} column of {_op_label(op)}: "
                                       f"{prop} from {_show(old)} to {_show(edit['value'])}.")
            elif kind == "add":
                page = int(edit.get("page", 1)) - 1
                if not 0 <= page < len(result["pages"]):
                    raise PatchError(f"{where}: the design has {len(result['pages'])} pages")
                op = dict(edit["value"])
                layers = [layer for layer in result["pages"][page]["layers"] if layer["kind"] == "overlay"]
                if not layers:
                    layers = [{"kind": "overlay", "id": f"p{page + 1}.added", "ops": []}]
                    result["pages"][page]["layers"].append(layers[0])
                layers[-1]["ops"].append(op)
                applied.changes.append(f"Added {_op_label(op)} on page {page + 1}.")
            elif kind == "delete":
                op = _locate(result, edit["target"])
                if op.get("role") in GUARDED_ROLES:
                    applied.guarded.append(f"{_op_label(op)} is a {op['role']} area")
                for page in result["pages"]:
                    for layer in page["layers"]:
                        if layer["kind"] == "overlay":
                            layer["ops"] = [o for o in layer["ops"] if o["id"] != edit["target"]]
                applied.changes.append(f"Removed {_op_label(op)}.")
            elif kind == "set_text":
                key = edit["key"]
                entries = result.setdefault("owner_text", {})
                old = entries.get(key)
                if old is not None and old.get("guarded"):
                    applied.guarded.append(f"the {old.get('label') or key} wording is protected")
                value = edit["value"]
                if isinstance(value, str):
                    value = {**(old or {"label": key.replace("_", " ")}), "value": value}
                entries[key] = value
                applied.changes.append(f"Changed the {value.get('label') or key} wording." if old
                                       else f"Added the {value.get('label') or key} wording.")
            elif kind in ("add_box", "delete_box"):
                page = int(edit.get("page", 1)) - 1
                if not 0 <= page < len(result["pages"]):
                    raise PatchError(f"{where}: the design has {len(result['pages'])} pages")
                box = [float(v) for v in edit["value"]]
                layers = result["pages"][page]["layers"]
                remove = next((layer for layer in layers if layer["kind"] == "remove"), None)
                if kind == "add_box":
                    if remove is None:
                        remove = {"kind": "remove", "id": f"p{page + 1}.stale", "boxes": []}
                        layers.insert(0, remove)
                    remove["boxes"].append(box)
                    applied.changes.append(f"Cleared the template's own text in {box} on page {page + 1}.")
                else:
                    if remove is None or box not in remove["boxes"]:
                        raise PatchError(f"{where}: page {page + 1} does not clear {box}")
                    remove["boxes"].remove(box)
                    if not remove["boxes"]:
                        layers.remove(remove)
                    applied.changes.append(f"Stopped clearing {box} on page {page + 1}.")
            else:
                raise PatchError(f"{where}: unknown edit {kind!r}")
        except KeyError as error:
            raise PatchError(f"{where}: missing {error.args[0]!r}") from error
        except (TypeError, ValueError) as error:
            if isinstance(error, PatchError):
                raise
            raise PatchError(f"{where}: {error}") from error
    try:
        validate_spec(result)
    except SpecError as error:
        raise PatchError(f"The edited design is not valid: {error}") from error
    applied.guarded = sorted(set(applied.guarded))
    if applied.guarded and not allow_guarded:
        raise PatchError("These edits change protected parts and need the owner's confirmation: "
                         + "; ".join(applied.guarded))
    return applied


def needs_confirmation(applied: Applied) -> bool:
    return bool(applied.guarded)


def guard_owner_text(spec: dict[str, Any], keys: Sequence[str]) -> None:
    """Mark owner-text entries (a disclaimer, a benchmark) as protected from edits without confirmation."""
    for key in keys:
        if key in spec.get("owner_text", {}):
            spec["owner_text"][key]["guarded"] = True
