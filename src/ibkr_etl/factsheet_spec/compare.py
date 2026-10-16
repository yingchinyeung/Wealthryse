"""Compare two factsheets by what they print, never by reading pixels.

- ``against_example``: the builder's score. Every span the completed example
  prints is looked for in the draft's render at the same place, with the same
  size, font family and colour. Values that change month to month are matched by
  place and shape (a percentage where a percentage was), not by their digits,
  because the draft may be rendered for another month.
- ``changed_regions``: the editor's before-and-after. The boxes on each page where
  two renders print something different, with a plain description of each.
- ``template_changes``: what moved between an old and a revised template, and
  which spec operations sit in the areas that changed.

The output is a list of findings with page, box and a sentence a person (or the
builder agent) can act on.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from .reader import Inventory, Span, _figure_kind, inventory
from .draft import standard_font

PLACE = 1.2      # points a span may sit from where the example prints it
SIZE = 0.15      # points of font size difference that still count as the same


def _family(font: str) -> str:
    return standard_font(font).split("-")[0]


def _shape(text: str) -> str:
    kind = _figure_kind(text)
    return kind or "words"


def _near(a: Span, b: Span, tolerance: float = PLACE) -> bool:
    if a.page != b.page or abs(a.y - b.y) > tolerance:
        return False
    return (abs(a.x - b.x) <= tolerance or abs(a.right - b.right) <= tolerance
            or abs((a.x + a.right) / 2 - (b.x + b.right) / 2) <= tolerance)


def _describe(span: Span) -> str:
    return f"{span.text[:50]!r} ({span.size:g}pt at {span.x:.1f}, {span.y:.1f})"


def against_example(rendered: Inventory, example: Inventory, *, ignore: Sequence[Mapping[str, Any]] = ()
                    ) -> dict[str, Any]:
    """How closely a render reproduces the completed example: findings, and the share of spans matched.

    ``ignore`` lists areas (page, box) the comparison skips, such as charts, whose
    lines and labels depend on the month.
    """
    def ignored(span: Span) -> bool:
        return any(area["page"] == span.page and area["box"][0] - 2 <= span.x <= area["box"][0] + area["box"][2] + 2
                   and area["box"][1] - 2 <= span.y <= area["box"][1] + area["box"][3] + 2 for area in ignore)

    wanted = [s for s in example.spans() if not ignored(s)]
    have = [s for s in rendered.spans() if not ignored(s)]
    used: set[int] = set()
    findings: list[dict[str, Any]] = []
    matched = 0
    for span in wanted:
        exact = next((i for i, h in enumerate(have) if i not in used and h.text == span.text and _near(h, span)), None)
        if exact is None:
            exact = next((i for i, h in enumerate(have) if i not in used and _near(h, span)
                          and _shape(h.text) == _shape(span.text) and _shape(span.text) != "words"), None)
        if exact is not None:
            used.add(exact)
            got = have[exact]
            problems = []
            if abs(got.size - span.size) > SIZE:
                problems.append(f"size {got.size:g}pt, the example uses {span.size:g}pt")
            if _family(got.font) != _family(span.font) or ("Bold" in standard_font(got.font)) != (
                    "Bold" in standard_font(span.font)):
                problems.append(f"font {standard_font(got.font)}, the example uses {standard_font(span.font)}")
            if got.color != span.color:
                problems.append(f"colour {got.color}, the example uses {span.color}")
            if problems:
                findings.append({"kind": "style", "page": span.page + 1, "box": span.box, "text": span.text[:60],
                                 "detail": f"{_describe(span)}: " + "; ".join(problems)})
            else:
                matched += 1
            continue
        moved = next((i for i, h in enumerate(have) if i not in used and h.text == span.text and h.page == span.page
                      and abs(h.y - span.y) < 40 and abs(h.x - span.x) < 120), None)
        if moved is not None:
            used.add(moved)
            got = have[moved]
            findings.append({"kind": "moved", "page": span.page + 1, "box": span.box, "text": span.text[:60],
                             "dx": round(span.x - got.x, 2), "dy": round(span.y - got.y, 2),
                             "detail": f"{_describe(span)} is drawn {span.x - got.x:+.1f}pt across and "
                                       f"{span.y - got.y:+.1f}pt up from where the example prints it"})
            continue
        findings.append({"kind": "missing", "page": span.page + 1, "box": span.box, "text": span.text[:60],
                         "detail": f"The example prints {_describe(span)}; the render prints nothing like it there"})
    for index, span in enumerate(have):
        if index not in used and not any(_near(span, w, 3.0) for w in wanted):
            findings.append({"kind": "extra", "page": span.page + 1, "box": span.box, "text": span.text[:60],
                             "detail": f"The render prints {_describe(span)}, which the example does not"})
    return {"spans": len(wanted), "matched": matched, "score": round(matched / max(1, len(wanted)), 4),
            "findings": findings}


def _span_key(span: Span) -> tuple[Any, ...]:
    return (span.page, span.text, round(span.x, 1), round(span.y, 1), round(span.size, 1), span.font, span.color)


def changed_regions(before: Inventory, after: Inventory) -> list[dict[str, Any]]:
    """Where two renders differ, as merged boxes per page with what changed inside each."""
    old = {_span_key(s): s for s in before.spans()}
    new = {_span_key(s): s for s in after.spans()}
    changes = [("removed", s) for k, s in old.items() if k not in new] + [("added", s) for k, s in new.items()
                                                                            if k not in old]
    for page_number, (a, b) in enumerate(zip(before.pages, after.pages)):
        for kind, fills in (("removed", [f for f in a.fills if f not in b.fills]),
                            ("added", [f for f in b.fills if f not in a.fills])):
            for fill in fills:
                changes.append((kind, Span(page_number, "", fill["box"][0], fill["box"][1] + 0.22 * 1,
                                           fill["box"][2], 1.0, "", fill["color"])))
    regions: list[dict[str, Any]] = []
    for kind, span in sorted(changes, key=lambda c: (c[1].page, -c[1].y, c[1].x)):
        box = span.box if span.text else [span.x, span.y - 0.22, span.width, 1.0]
        for region in regions:
            x, y, w, h = region["box"]
            if region["page"] == span.page + 1 and box[0] < x + w + 12 and x - 12 < box[0] + box[2] \
                    and box[1] < y + h + 12 and y - 12 < box[1] + box[3]:
                x0, y0 = min(x, box[0]), min(y, box[1])
                x1, y1 = max(x + w, box[0] + box[2]), max(y + h, box[1] + box[3])
                region["box"] = [round(x0, 2), round(y0, 2), round(x1 - x0, 2), round(y1 - y0, 2)]
                region[kind].append(span.text)
                break
        else:
            regions.append({"page": span.page + 1, "box": [round(v, 2) for v in box],
                            "added": [span.text] if kind == "added" else [],
                            "removed": [span.text] if kind == "removed" else []})
    for region in regions:
        added = [t for t in region["added"] if t]
        removed = [t for t in region["removed"] if t]
        if added and removed:
            region["summary"] = f"{removed[0][:40]!r} became {added[0][:40]!r}" + (
                f" and {len(added) - 1} more" if len(added) > 1 else "")
        elif added:
            region["summary"] = f"Now prints {added[0][:40]!r}" + (f" and {len(added) - 1} more" if len(added) > 1 else "")
        elif removed:
            region["summary"] = f"No longer prints {removed[0][:40]!r}" + (
                f" and {len(removed) - 1} more" if len(removed) > 1 else "")
        else:
            region["summary"] = "Shapes or colours changed"
    return regions


def template_changes(old_template: str, new_template: str, spec: Mapping[str, Any]) -> dict[str, Any]:
    """A revised template against the one a spec was measured from: what changed, and what it touches."""
    old_inv, new_inv = inventory(old_template), inventory(new_template)
    regions = changed_regions(old_inv, new_inv)
    touched = []
    for page_number, page in enumerate(spec["pages"]):
        for layer in page["layers"]:
            for op in layer.get("ops", []) if layer["kind"] == "overlay" else []:
                box = op.get("box") or ([op["x"] - 2, op["y"] - 2, 60, (op.get("size") or 8) + 4] if "y" in op else
                                        [op["x"], op["top"] - 200, 340, 200] if "top" in op else None)
                if box is None:
                    continue
                for region in regions:
                    x, y, w, h = region["box"]
                    if region["page"] == page_number + 1 and box[0] < x + w and x < box[0] + box[2] \
                            and box[1] < y + h and y < box[1] + box[3]:
                        touched.append({"op": op["id"], "page": page_number + 1, "region": region["summary"]})
    same_pages = len(old_inv.pages) == len(new_inv.pages) and all(
        abs(a.size[0] - b.size[0]) < 1 and abs(a.size[1] - b.size[1]) < 1 for a, b in zip(old_inv.pages, new_inv.pages))
    return {"old_sha256": old_inv.sha256, "new_sha256": new_inv.sha256, "same_page_layout": same_pages,
            "regions": regions, "operations_touched": touched}


def summarize(findings: Iterable[Mapping[str, Any]], limit: int = 40) -> list[str]:
    return [f"page {f['page']}: {f['detail']}" for f in list(findings)[:limit]]
