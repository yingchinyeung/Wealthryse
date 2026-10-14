"""The composition tree on page 2 of an Alpha World design factsheet.

Draws a composite's long, short and cash groups (composition_tree.composite_tree)
in the area MS and T use for their asset allocation diagram, in the same visual
vocabulary: black boxes, white bold labels and thin connecting lines. The layout
is computed first, so it can be checked without rendering.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from reportlab.lib.colors import black, white
from reportlab.pdfbase.pdfmetrics import stringWidth

# The MS and T allocation areas (monthly_artifacts.ALPHA_WORLD_MS_ALLOCATION_LAYOUT
# and alpha_world_t_factsheet.REGIONS) share these rows.
AREA = (95.0, 607.0, 410.0, 148.0)
PARENT_Y, PARENT_HEIGHT, PARENT_MAX_WIDTH = 710.0, 41.0, 140.0
CHILD_Y, CHILD_HEIGHT, CHILD_MAX_WIDTH = 612.0, 55.0, 91.0
MARGIN, ITEM_GAP, GROUP_GAP = 4.0, 9.5, 15.5
MINIMUM_CHILD_WIDTH = 40.0
PARENT_FONT, HEADING_FONT, CAPTION_FONT = ("Helvetica-Bold", 12.0), ("Helvetica-Bold", 10.8), ("Helvetica", 9.4)
MINIMUM_FONT = 5.5
CAPTION_LINE_STEP = 11.0


@dataclass(frozen=True)
class Text:
    value: str
    center_x: float
    y: float
    font: str
    size: float


@dataclass
class TreeLayout:
    boxes: list[tuple[float, float, float, float]] = field(default_factory=list)
    lines: list[tuple[float, float, float, float]] = field(default_factory=list)
    texts: list[Text] = field(default_factory=list)


def percent(value: float) -> str:
    rounded = round(value)
    return f"{rounded:.0f}%" if abs(value - rounded) < .05 else f"{value:.1f}%"


def _fit(text: str, font: str, size: float, width: float) -> float:
    while size > MINIMUM_FONT and stringWidth(text, font, size) > width:
        size = round(size - .1, 1)
    if stringWidth(text, font, size) > width:
        raise ValueError(f"'{text}' does not fit the factsheet composition tree")
    return size


def _splits(words: list[str], parts: int) -> list[list[str]]:
    if parts == 1:
        return [[" ".join(words)]]
    return [[" ".join(words[:i])] + rest for i in range(1, len(words) - parts + 2)
            for rest in _splits(words[i:], parts - 1)]


def _caption(name: str, width: float) -> tuple[list[str], float]:
    """Up to three lines, never truncated: the largest size that fits, preferring
    fewer and more even lines when that costs at most half a point."""
    font, size = CAPTION_FONT
    words = name.split()
    options = []
    for parts in range(1, min(3, len(words)) + 1):
        for lines in _splits(words, parts):
            fitted = size
            try:
                for line in lines:
                    fitted = min(fitted, _fit(line, font, size, width))
            except ValueError:
                continue
            widest = max(stringWidth(line, font, fitted) for line in lines)
            options.append((fitted, len(lines), widest, lines))
    if not options:
        raise ValueError(f"'{name}' does not fit the factsheet composition tree")
    largest = max(option[0] for option in options)
    fitted, _, _, lines = min((o for o in options if o[0] >= largest - .5), key=lambda o: (o[1], o[2], -o[0]))
    return lines, fitted


def _parent_label(label: str, share: str, width: float) -> list[tuple[str, float, float]]:
    """(text, size, offset from the box's text baseline): one line, or two when one is too wide."""
    font, size = PARENT_FONT
    one = f"{label} {share}"
    if stringWidth(one, font, size) <= width:
        return [(one, size, 0.0)]
    fitted = min(_fit(label, font, size, width), _fit(share, font, size, width))
    return [(label, fitted, fitted * .55), (share, fitted, -fitted * .65)]


def layout(tree: Mapping[str, Any], area: tuple[float, float, float, float] = AREA) -> TreeLayout:
    groups = [group for group in tree["groups"] if group["items"]]
    count = sum(len(group["items"]) for group in groups)
    gaps = ITEM_GAP * (count - len(groups)) + GROUP_GAP * (len(groups) - 1)
    child_width = min(CHILD_MAX_WIDTH, (area[2] - 2 * MARGIN - gaps) / count)
    if child_width < MINIMUM_CHILD_WIDTH:
        raise ValueError("The composition has too many sleeves for the factsheet tree")
    total = count * child_width + gaps
    x = area[0] + (area[2] - total) / 2
    result = TreeLayout()
    padding = 6.0
    for index, group in enumerate(groups):
        if index:
            x += GROUP_GAP - ITEM_GAP
        centers = []
        for item in group["items"]:
            result.boxes.append((x, CHILD_Y, child_width, CHILD_HEIGHT))
            center = x + child_width / 2
            centers.append(center)
            heading = percent(item["share"])
            lines, size = _caption(str(item["name"]), child_width - padding)
            # A three-line caption moves down and the heading up, keeping a clear gap between them.
            heading_y, caption_y = (41.0, 13.5) if len(lines) == 3 else (37.0, 15.5)
            result.texts.append(Text(heading, center, CHILD_Y + heading_y, HEADING_FONT[0],
                                     _fit(heading, *HEADING_FONT, child_width - padding)))
            step = min(CAPTION_LINE_STEP if len(lines) < 3 else 10.0, size * 1.15)
            for line_number, line in enumerate(lines):
                y = CHILD_Y + caption_y + ((len(lines) - 1) / 2 - line_number) * step
                result.texts.append(Text(line, center, y, CAPTION_FONT[0], size))
            x += child_width + ITEM_GAP
        span = centers[-1] - centers[0] + child_width
        parent_width = min(PARENT_MAX_WIDTH, max(span, child_width))
        parent_center = (centers[0] + centers[-1]) / 2
        result.boxes.append((parent_center - parent_width / 2, PARENT_Y, parent_width, PARENT_HEIGHT))
        for text, size, offset in _parent_label(group["label"].upper(), percent(group["share"]),
                                                parent_width - padding):
            result.texts.append(Text(text, parent_center, PARENT_Y + 16.2 + offset, PARENT_FONT[0], size))
        for center in centers:
            result.lines.append((parent_center, PARENT_Y, center, CHILD_Y + CHILD_HEIGHT))
    return result


def draw(c, tree: Mapping[str, Any], area: tuple[float, float, float, float] = AREA) -> TreeLayout:
    """Clear the area and draw the tree; returns what was drawn."""
    shapes = layout(tree, area)
    c.saveState()
    c.setFillColor(white)
    c.rect(*area, fill=1, stroke=0)
    c.setStrokeColor(black)
    c.setLineWidth(.6)
    for line in shapes.lines:
        c.line(*line)
    c.setFillColor(black)
    for box in shapes.boxes:
        c.rect(*box, fill=1, stroke=0)
    c.setFillColor(white)
    for text in shapes.texts:
        c.setFont(text.font, text.size)
        c.drawCentredString(text.center_x, text.y, text.value)
    c.restoreState()
    return shapes
