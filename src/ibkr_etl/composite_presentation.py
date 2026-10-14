"""The monthly deck of a composite in the Alpha World MS or T design.

MS and T share the approved 20-slide v24 deck. A composite's deck takes MS's
HKD path through render_monthly_presentation.mjs under its own name
(render_composite_presentation.mjs adapts it, leaving that renderer unchanged),
with numbers from profiled_composite.analyze on its frozen snapshot: the same
arithmetic as its factsheet and the website.

Slide 18, the template's fee terms, becomes the composite's own page: its
description and its composition tree, drawn as on its factsheet. T's deck
already replaces that slide with its model basis.

Usage: python -m ibkr_etl.composite_presentation snapshot.json   (the rendering model)
       python -m ibkr_etl.composite_presentation compose in.pptx out.pptx snapshot.json
"""

from __future__ import annotations

from datetime import date
import hashlib
import html
import io
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Mapping
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape
import zipfile

from reportlab.pdfbase.pdfmetrics import stringWidth

from . import factsheet_tree
from .profiled_composite import _profile, analyze

VERSION = "alpha-world-design-composite-presentation-v1"
CONTRACT_VERSION = "alpha-world-design-composite-v24-hkd-hibor-v1"
CONTENT_REQUIREMENTS = (
    "The composite's own name on every strategy header; its description and composition tree on slide 18.",
    "Qualified HKD NAV from the composite's inception and inception-to-date average published 1M HIBOR.",
    "All six exported charts and seven risk metrics match the frozen composite snapshot.",
    "Six embedded chart-data workbooks match every category, value and series header.",
    "Fixed-unit component effects sum to the monthly NAV return.",
    "Benchmark results and crisis replay remain unavailable; NAV interest is not charged twice.",
    "No Alpha World MS or T identity and no reference-account text survives, in slides or notes.",
)
UNAVAILABLE = ("benchmark_total_return", "benchmark_annualized_return", "benchmark_annualized_volatility",
               "benchmark_max_drawdown", "excess_total_return", "beta", "correlation", "tracking_error",
               "information_ratio", "relative_win_rate", "upside_capture_pct")
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"


def _require(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def _long_date(day: date) -> str:
    return f"{day.day} {day:%B %Y}"


def inception_note(inception: str) -> str:
    """Slide 15's note under the monthly returns, as MS words it for its own inception."""

    day = date.fromisoformat(inception)
    if day.day == 1:
        return f"Returns start at inception on {_long_date(day)}. Monthly returns use observed HKD NAV."
    return f"{day:%B %Y} is a partial month from inception on {day.day} {day:%B}. Monthly returns use observed HKD NAV."


def speaker_note(profile, period_end: str) -> str:
    """Every slide's notes; the template's own notes describe the reference account.

    They never name the design: no MS or T identity survives in the deck.
    """

    return (f"{profile.display_name}, {period_end}. HKD fixed-unit composite from "
            f"{_long_date(date.fromisoformat(profile.inception))}. Source: its approved definition, its frozen "
            "month-end snapshot and published 1M HIBOR fixings. Underlying account NAV includes its own cash and "
            "interest; no additional financing is charged. Benchmark comparisons and crisis replay are unavailable. "
            "Private review; internal use only.")


# Rendering model ------------------------------------------------------------


def build_model(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """MS's rendering model (alpha_world_ms_presentation) for a composite, with its identity."""

    profile = _profile(snapshot)
    _require(snapshot.get("reporting_currency") == "HKD", "The composite's deck reports in HKD")
    _require(snapshot.get("publication_allowed") is False and snapshot.get("quality_status") != "fail",
             "The composite's deck needs its private, non-failed frozen snapshot")
    metrics, daily, evidence = analyze(snapshot)
    _require(snapshot["risk"]["metrics"] == metrics, "The composite's frozen metrics differ from recomputation")
    _require(snapshot["risk"].get("crisis_replay") is None,
             "Crisis replay is not yet qualified for Alpha World design composites")
    holdings = snapshot["holdings"]["holdings"]
    values = [float(row["market_value_text"]) for row in holdings]
    _require(all(row.get("currency") == "HKD" for row in holdings) and all(math.isfinite(v) for v in values),
             "The composite's component values must be finite HKD observations")
    series = snapshot["performance"]["series"]
    nav = float(snapshot["portfolio_summary"]["latest_nav"]["total_text"])
    _require(math.isclose(sum(values), nav, abs_tol=1e-9, rel_tol=0)
             and math.isclose(float(series[-1]["nav"]), nav, abs_tol=1e-9, rel_tol=0),
             "The composite's signed components do not reconcile to NAV")
    sector = snapshot["sector_allocation"]
    _require(math.isclose(sum(abs(v) for v in values), float(sector["gross_market_value"]), abs_tol=1e-9, rel_tol=0)
             and math.isclose(sum(float(r["weight"]) for r in sector["allocation"]), 1., abs_tol=1e-10),
             "The composite's gross exposure or component-type weights differ")
    # The tree on slide 18 and the tables show the same sleeves.
    groups = profile.composition["groups"]
    drawn = sorted(str(item["name"]) for group in groups if group["label"] != "Cash" for item in group["items"])
    held = sorted(str(row["description"]) for row in holdings if row.get("asset_category") != "cash")
    cash = [row for row in holdings if row.get("asset_category") == "cash"]
    cash_group = [group for group in groups if group["label"] == "Cash"]
    _require(drawn == held and len(cash) == 1 and len(cash_group) == 1,
             "The composite's sleeves differ from its approved composition")
    constant_cash = float(cash[0]["market_value_text"])
    _require(math.isclose(float(cash_group[0]["items"][0]["exposure"]), constant_cash, abs_tol=1e-9),
             "The composite's constant cash differs from its approved composition")
    for name in ("performance_comparison", "secondary_performance_comparison"):
        _require(not (snapshot.get(name) or {}).get("metrics"),
                 "The composite's benchmark results need separate qualification")
    months: dict[str, Mapping[str, Any]] = {}
    for row in series:
        months[row["date"][:7]] = row
    previous, month_rows = float(series[0]["nav"]), []
    for row in months.values():
        value = float(row["nav"])
        month_rows.append({"date": row["date"], "portfolio": value, "portfolio_monthly_return": value / previous - 1,
                           "benchmark_monthly_return": None})
        previous = value
    supplied = snapshot["month_end_performance"]
    _require(len(supplied) == len(month_rows) and all(
        a["date"] == b["date"] and float(a["portfolio"]) == b["portfolio"] for a, b in zip(supplied, month_rows)),
        "The composite's monthly NAV differs from its full history")
    monthly = snapshot["monthly_reporting"]
    _require(monthly.get("available") is True and monthly["period_end"] == snapshot["data_as_of"],
             "Dated monthly reporting inputs are required")
    monthly_return = float(monthly["portfolio_monthly_return"])
    _require(math.isclose(monthly_return, month_rows[-1]["portfolio_monthly_return"], abs_tol=1e-12, rel_tol=0)
             and math.isclose(sum(float(r["contribution"]) for r in monthly["security_contributions"]),
                              monthly_return, abs_tol=1e-10, rel_tol=0)
             and monthly["residual_contribution"] == 0,
             "The composite's component contributions do not reconcile")
    primary = {"period_start": evidence["start"], "period_end": evidence["end"], "return_observations": len(daily),
               "portfolio_total_return": metrics["total_return"],
               "portfolio_annualized_return": metrics["annualized_return"],
               "portfolio_annualized_volatility": metrics["annualized_volatility"],
               "portfolio_max_drawdown": metrics["maximum_drawdown"], **{name: None for name in UNAVAILABLE}}
    risk = {**metrics, "daily_expected_tail_loss": metrics["cvar_95_daily"], "return_observations": len(daily)}
    risk.update({name: None for name in (
        "blue_mean_daily_return", "blue_standard_error", "expected_maximum_drawdown_1y", "actual_maximum_drawdown_1y",
        "tail_maximum_drawdown_1y", "crisis_maximum_drawdown", "drawdown_simulation_paths",
        "drawdown_horizon_sessions", "drawdown_block_length", "drawdown_tail_confidence")})
    # As MS: the one-year drawdown bootstrap only where no return interval spans a missing session.
    if not evidence["excluded_intervals"] and len(daily) >= 2:
        from .alpha_world_ms_runtime import extended_metrics
        extended = extended_metrics(tuple(daily))
        risk.update({name: extended[name] for name in (
            "expected_maximum_drawdown_1y", "actual_maximum_drawdown_1y", "tail_maximum_drawdown_1y",
            "drawdown_simulation_paths", "drawdown_horizon_sessions", "drawdown_block_length",
            "drawdown_tail_confidence")})
    return {
        "version": VERSION, "calculation": evidence, "primary": primary,
        "secondary": {name: None for name in UNAVAILABLE}, "risk": risk, "month_rows": month_rows,
        "constant_cash": constant_cash, "benchmark_available": False, "crisis_available": False,
        "risk_free_description": "1M HIBOR, inception-to-valuation period average",
        "source_value_sha256": evidence["source_value_sha256"],
        "identity": {"subject_id": profile.subject_id, "style": profile.style, "display_name": profile.display_name,
                     "strategy_name": f"iStrategy: {profile.display_name}", "description": profile.description,
                     "inception": profile.inception, "inception_note": inception_note(profile.inception),
                     "speaker_note": speaker_note(profile, str(snapshot["period_end"]))},
        "composition": dict(profile.composition),
    }


# Slide 18 -------------------------------------------------------------------

SLIDE = "ppt/slides/slide18.xml"
EMU = 12700  # per point
# The v24 template's slide 18 shapes, by their ids, and the text each starts with.
KEPT = {"14": "iStrategy Fees", "8": "Terms and Conditions", "572": "Rebalancing and Updates", "573": "Notes",
        "588": "Performance fees calculated"}
REMOVED = {"560": "", "569": "", "574": "", "576": "Rebalancing", "577": "Market Updates",
           "578": "Rebalancing Explanations", "601": "", "602": "", "603": ""}
HEADER_WIDTH, HEADER_SIZE = 4838625, 18.0  # every other slide's strategy header, as it inherits its size
NAMED_SHAPES = 17  # the cover name and the strategy headers of slides 3 to 18
LEFT, WIDTH = 457920, 8332920  # the template's two columns, end to end
ABOUT_BAR_Y, ABOUT_TEXT_Y, ABOUT_TEXT_HEIGHT = 760000, 1130000, 1330000
TREE_BAR_Y, TREE_TOP = 2540000, 2930000
FOOTER_TOP = 4913854
BAR_HEIGHT = 333000
TEXT_INSETS = (91440, 45720)
ABOUT_SIZES = (12.0, 11.5, 11.0, 10.5, 10.0, 9.5, 9.0, 8.5, 8.0)
GREEN, PANEL, LINE, INK = "087C48", "F2F2F2", "5D6878", "000000"
TITLES = ("Portfolio composition", "About this portfolio", "Composition at inception (share of starting NAV)")


def _text(element) -> str:
    return "".join(node.text or "" for node in element.iter(f"{{{A}}}t"))


def _wrapped_lines(text: str, size: float, width: float) -> int:
    lines, line = 0, ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if line and stringWidth(candidate, "Helvetica", size) > width:
            lines, line = lines + 1, word
        else:
            line = candidate
    return lines + (1 if line else 0)


def about_size(description: str) -> float:
    """The largest size at which the description fills the full-width About panel, with room to spare.

    Arial and Helvetica share their widths. Any text that fits the factsheet's
    About column (13 lines of 337pt at 8pt) needs at most 7 lines here at 8pt.
    """

    width = (WIDTH - 2 * TEXT_INSETS[0]) / EMU * .95
    height = (ABOUT_TEXT_HEIGHT - 2 * TEXT_INSETS[1]) / EMU * .9
    for size in ABOUT_SIZES:
        if _wrapped_lines(description, size, width) * size * 1.2 <= height:
            return size
    raise ValueError("The composite's description does not fit its deck page")


def _set_text(shape, text: str, size: float | None = None) -> None:
    """Keep the first paragraph and run, with their formatting; replace the text."""

    body = shape.find(f"{{{P}}}txBody")
    paragraphs = body.findall(f"{{{A}}}p")
    for extra in paragraphs[1:]:
        body.remove(extra)
    runs = paragraphs[0].findall(f"{{{A}}}r")
    _require(bool(runs), "A slide 18 text has no run to keep")
    for extra in runs[1:]:
        paragraphs[0].remove(extra)
    runs[0].find(f"{{{A}}}t").text = text
    if size is not None:
        runs[0].find(f"{{{A}}}rPr").set("sz", str(round(size * 100)))


def _place(shape, x: int, y: int, cx: int | None = None, cy: int | None = None) -> None:
    transform = shape.find(f"{{{P}}}spPr/{{{A}}}xfrm")
    transform.find(f"{{{A}}}off").set("x", str(x))
    transform.find(f"{{{A}}}off").set("y", str(y))
    if cx is not None:
        transform.find(f"{{{A}}}ext").set("cx", str(cx))
    if cy is not None:
        transform.find(f"{{{A}}}ext").set("cy", str(cy))


def _run(text: str, size: float, bold: bool, color: str) -> str:
    return (f'<a:p><a:pPr algn="ctr"><a:lnSpc><a:spcPct val="100000"/></a:lnSpc><a:buNone/></a:pPr>'
            f'<a:r><a:rPr lang="en-US" sz="{round(size * 100)}" b="{1 if bold else 0}" spc="0">'
            f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill><a:latin typeface="Arial"/>'
            f'<a:ea typeface="Arial"/><a:cs typeface="Arial"/></a:rPr><a:t>{escape(text)}</a:t></a:r></a:p>')


def tree_shapes(composition: Mapping[str, Any], first_id: int) -> tuple[list[str], dict[str, Any]]:
    """The factsheet tree's exact layout as native shapes: green group boxes, light sleeve boxes."""

    layout = factsheet_tree.layout(composition)
    area_x, area_y, area_width, area_height = factsheet_tree.AREA
    left = round((9144000 - area_width * EMU) / 2)
    top_of_area = area_y + area_height

    def x(value):
        return left + round((value - area_x) * EMU)

    def y(value):
        return TREE_TOP + round((top_of_area - value) * EMU)

    shapes, next_id = [], first_id
    for x1, y1, x2, y2 in layout.lines:
        off_x, off_y = min(x(x1), x(x2)), min(y(y1), y(y2))
        flip = ' flipH="1"' if x(x2) < x(x1) else ""
        shapes.append(
            f'<p:cxnSp><p:nvCxnSpPr><p:cNvPr id="{next_id}" name="Composition line {next_id}"/><p:cNvCxnSpPr/>'
            f'<p:nvPr/></p:nvCxnSpPr><p:spPr><a:xfrm{flip}><a:off x="{off_x}" y="{off_y}"/>'
            f'<a:ext cx="{abs(x(x2) - x(x1))}" cy="{abs(y(y2) - y(y1))}"/></a:xfrm><a:prstGeom prst="line">'
            f'<a:avLst/></a:prstGeom><a:ln w="9525"><a:solidFill><a:srgbClr val="{LINE}"/></a:solidFill></a:ln>'
            f'</p:spPr></p:cxnSp>')
        next_id += 1
    texts = list(layout.texts)
    for bx, by, bw, bh in layout.boxes:
        inside = sorted((t for t in texts if bx <= t.center_x <= bx + bw and by <= t.y <= by + bh),
                        key=lambda t: -t.y)
        _require(bool(inside), "A composition box has no text")
        parent = by == factsheet_tree.PARENT_Y
        fill, color = (GREEN, "FFFFFF") if parent else (PANEL, INK)
        paragraphs = "".join(_run(t.value, t.size, t.font.endswith("Bold"), color) for t in inside)
        shapes.append(
            f'<p:sp><p:nvSpPr><p:cNvPr id="{next_id}" name="Composition box {next_id}"/><p:cNvSpPr/><p:nvPr/>'
            f'</p:nvSpPr><p:spPr><a:xfrm><a:off x="{x(bx)}" y="{y(by + bh)}"/>'
            f'<a:ext cx="{round(bw * EMU)}" cy="{round(bh * EMU)}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/>'
            f'</a:prstGeom><a:solidFill><a:srgbClr val="{fill}"/></a:solidFill><a:ln><a:noFill/></a:ln></p:spPr>'
            f'<p:txBody><a:bodyPr wrap="none" lIns="0" tIns="0" rIns="0" bIns="0" anchor="ctr"><a:noAutofit/>'
            f'</a:bodyPr><a:lstStyle/>{paragraphs}</p:txBody></p:sp>')
        next_id += 1
    _require(sum(len([t for t in texts if bx <= t.center_x <= bx + bw and by <= t.y <= by + bh])
                 for bx, by, bw, bh in layout.boxes) == len(texts), "A composition text sits outside its box")
    bottom = max(y(by) for _, by, _, _ in layout.boxes)
    _require(bottom < FOOTER_TOP, "The composition tree does not fit above the footer")
    return shapes, {"boxes": len(layout.boxes), "lines": len(layout.lines), "texts": [t.value for t in texts]}


def _namespaces(data: bytes) -> None:
    for _, (prefix, uri) in ET.iterparse(io.BytesIO(data), events=("start-ns",)):
        if prefix:
            ET.register_namespace(prefix, uri)


def compose_slide(data: bytes, model: Mapping[str, Any]) -> tuple[bytes, dict[str, Any]]:
    """Slide 18 of the exported deck, rebuilt as the composite's page."""

    identity = model["identity"]
    _namespaces(data)
    root = ET.fromstring(data)
    tree = root.find(f"{{{P}}}cSld/{{{P}}}spTree")
    shapes: dict[str, Any] = {}
    for child in list(tree):
        properties = child.find(f".//{{{P}}}cNvPr")
        if properties is None or properties.get("id") not in {**KEPT, **REMOVED}:
            continue
        _require(properties.get("id") not in shapes, "Slide 18 repeats a template shape")
        shapes[properties.get("id")] = child
    _require(set(shapes) == set(KEPT) | set(REMOVED), "Slide 18 is not the approved template's fee page")
    for shape_id, start in {**KEPT, **REMOVED}.items():
        _require(_text(shapes[shape_id]).replace("\xa0", " ").strip().startswith(start),
                 "Slide 18 is not the approved template's fee page")
    for shape_id in REMOVED:
        tree.remove(shapes[shape_id])
    size = about_size(identity["description"])
    _set_text(shapes["14"], identity["strategy_name"], HEADER_SIZE)
    _place(shapes["14"], 205086, 70198, HEADER_WIDTH)
    _set_text(shapes["8"], TITLES[0])
    _set_text(shapes["572"], TITLES[1])
    _place(shapes["572"], LEFT, ABOUT_BAR_Y, WIDTH, BAR_HEIGHT)
    _set_text(shapes["588"], identity["description"], size)
    _place(shapes["588"], LEFT, ABOUT_TEXT_Y, WIDTH, ABOUT_TEXT_HEIGHT)
    _set_text(shapes["573"], TITLES[2])
    _place(shapes["573"], LEFT, TREE_BAR_Y, WIDTH, BAR_HEIGHT)
    used = [int(e.get("id")) for e in root.iter(f"{{{P}}}cNvPr") if str(e.get("id", "")).isdigit()]
    added, drawn = tree_shapes(model["composition"], max(used) + 1)
    wrapper = ET.fromstring(f'<p:spTree xmlns:p="{P}" xmlns:a="{A}">{"".join(added)}</p:spTree>')
    for element in list(wrapper):
        tree.append(element)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True), {**drawn, "about_size": size}


SHAPE = re.compile(r"<p:sp>.*?</p:sp>", re.S)
RUN_PROPERTIES = re.compile(r"<a:rPr\b([^>]*?)(/?)>")
INHERITED_SIZE = 1800  # PowerPoint's size for a text box run that sets none


def fit_names(data: bytes, names: tuple[str, ...]) -> tuple[bytes, int]:
    """Shrink a strategy header or the cover name to fit one line; MS's own name always does.

    Helvetica Bold is at least as wide as the template's Aptos Display, so a
    name that fits by its widths fits on the slide.
    """

    fitted = [0]

    def fit(match):
        shape = match.group(0)
        name = html.unescape("".join(re.findall(r"<a:t>([^<]*)</a:t>", shape)))
        if name not in names:
            return shape
        body = re.search(r"<a:bodyPr\b[^>]*>", shape).group(0)
        insets = sum(int(found.group(1)) if (found := re.search(f'{side}="(\\d+)"', body)) else TEXT_INSETS[0]
                     for side in ("lIns", "rIns"))
        width = (int(re.search(r'<a:ext cx="(\d+)"', shape).group(1)) - insets) / EMU * .95
        largest = round(math.floor(width / stringWidth(name, "Helvetica-Bold", 1) * 2) / 2 * 100)
        fitted[0] += 1

        def run(properties):
            attributes, close = properties.group(1), properties.group(2)
            size = re.search(r'\bsz="(\d+)"', attributes)
            current = int(size.group(1)) if size else INHERITED_SIZE
            if current <= largest:
                return properties.group(0)
            if size:
                attributes = attributes.replace(size.group(0), f'sz="{largest}"')
            else:
                attributes += f' sz="{largest}"'
            return f"<a:rPr{attributes}{close}>"

        return RUN_PROPERTIES.sub(run, shape)

    return SHAPE.sub(fit, data.decode("utf-8")).encode("utf-8"), fitted[0]


def page_text(path: Path) -> tuple[str, int, int]:
    """Slide 18's text, and how many tree boxes and lines it carries."""

    with zipfile.ZipFile(path) as package:
        root = ET.fromstring(package.read(SLIDE))
    names = [str(e.get("name", "")) for e in root.iter(f"{{{P}}}cNvPr")]
    return (" ".join(_text(p) for p in root.iter(f"{{{A}}}p")),
            sum(name.startswith("Composition box ") for name in names),
            sum(name.startswith("Composition line ") for name in names))


def verify_page(path: Path, model: Mapping[str, Any]) -> dict[str, Any]:
    """Read slide 18 back: the composite's name, description and whole tree, and no fee terms."""

    identity = model["identity"]
    text, boxes, lines = page_text(path)
    layout = factsheet_tree.layout(model["composition"])
    required = [identity["strategy_name"], " ".join(identity["description"].split()), *TITLES,
                *(t.value for t in layout.texts)]
    missing = [value for value in required if value not in text]
    _require(not missing, f"Slide 18 is missing {missing[:1]}")
    _require((boxes, lines) == (len(layout.boxes), len(layout.lines)), "Slide 18's composition tree is incomplete")
    # The composite's own words may use a fee-page term; only what is left must not.
    rest = text
    for value in sorted(required, key=len, reverse=True):
        rest = rest.replace(value, " ", 1)
    for value in ("Performance fees", "Rebalancing", "iStrategy Fees", "Terms and Conditions", "Market Updates"):
        _require(value not in rest, f"Slide 18 still shows the template's {value!r}")
    return {"status": "passed", "slide": 18, "tree_boxes": boxes, "tree_lines": lines,
            "tree_texts": len(layout.texts), "description": True}


def compose(source: Path, destination: Path, snapshot: Mapping[str, Any]) -> dict[str, Any]:
    model = build_model(snapshot)
    with zipfile.ZipFile(source) as package:
        entries = [(info, package.read(info)) for info in package.infolist()]
    names = [info.filename for info, _ in entries]
    _require(names.count(SLIDE) == 1 and len(set(names)) == len(names), "The deck has no single slide 18")
    _require(len([n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]) == 20,
             "The deck does not have the approved 20 slides")
    destination = Path(destination)
    _require(not destination.exists() and destination.resolve() != Path(source).resolve(),
             "The composite deck output must be a new file")
    identity, named = model["identity"], 0
    try:
        with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as package:
            for info, data in entries:
                if info.filename == SLIDE:
                    data, drawn = compose_slide(data, model)
                if re.fullmatch(r"ppt/slides/slide\d+\.xml", info.filename):
                    data, count = fit_names(data, (identity["strategy_name"], identity["display_name"]))
                    named += count
                package.writestr(info, data)
        _require(named == NAMED_SHAPES, "The composite's name is missing from a header or the cover")
        receipt = verify_page(destination, model)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return {**receipt, "about_size": drawn["about_size"], "named_shapes": named,
            "output_sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}


def main() -> None:
    if sys.argv[1:2] == ["compose"]:
        source, destination, snapshot = sys.argv[2:5]
        print(json.dumps(compose(Path(source), Path(destination), json.loads(Path(snapshot).read_bytes())),
                         sort_keys=True))
        return
    raw = Path(sys.argv[1]).read_bytes()
    model = build_model(json.loads(raw))
    model["snapshot_sha256"] = hashlib.sha256(raw).hexdigest()
    print(json.dumps(model, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
