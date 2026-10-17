"""Factsheets for composites in the Alpha World MS or T design.

The composite gets the chosen design's two pages under its own name: the MS
completed factsheet or the T owner template is the base, its variable text and
charts are removed (not just covered), and the approved MS sections are merged
back exactly as the T factsheet does. Only the identity changes: the title,
the inception month, the About text (the owner's description), the KPI panel
title and, on page 2, a tree of the composite's own sleeves. The subtitle,
benchmark, risk profile and disclaimer stay the design's own.

Every number comes from profiled_composite.analyze on the frozen snapshot, so
the factsheet shows exactly what the risk snapshot and the website show.
For internal and shareholder release, as MS and T; nothing here grants client publication.
"""
from __future__ import annotations

from datetime import date
import hashlib
import io
import json
import math
from pathlib import Path

from pypdf import PageObject, PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from reportlab.lib.colors import black, white
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from . import factsheet_tree
from .alpha_world_t_factsheet import (AUTHORITY_SHA256 as T_AUTHORITY_SHA256, HEADER_VALUES as T_HEADER_VALUES,
                                      REGIONS, _footer, _page_one, _strip_variables)
from .profiled_composite import analyze, methodology_identity
from .report_profiles import ReportProfile, snapshot_profile

LAYOUT_VERSION = "alpha-world-design-composite-factsheet-v1"
# The MS completed factsheet (monthly_artifacts.ALPHA_WORLD_MS_FACTSHEET_AUTHORITY_SHA256).
MS_AUTHORITY_SHA256 = "f12d94ec0f18a757263d6956789c1ae4718b52362d77abd126ebc813ff464909"
DESIGNS = {
    "alpha-world-ms": {"authority": MS_AUTHORITY_SHA256, "risk_profile": "Multi-Tranche",
                       "title": (22.8385, 782.0599, 18.0014)},
    "alpha-world-t": {"authority": T_AUTHORITY_SHA256, "risk_profile": T_HEADER_VALUES[3][1],
                      "title": (22.85, 784.3, 18.025)},
}
# Removed from the base on page 1 as well as REGIONS: the design's own title.
TITLE_REGION = (18., 777.5, 465., 23.)
TITLE_RIGHT = 487.
TITLE_MINIMUM = 12.
TITLE_COLOR = (0.0941, 0.0941, 0.098)
# Synthetic bold for a name the design's Georgia Bold subset cannot spell:
# stroke and letter spacing (per point of size) measured to match the real
# bold's weight and width on the letters the subset does carry.
SYNTHETIC_BOLD_STROKE, SYNTHETIC_BOLD_SPACING = .042, .055
# The approved MS About paragraphs, removed from the merged MS sections; the
# "About this Index" heading above them stays.
ABOUT_TEXT = (20., 520., 357., 150.)
ABOUT_X, ABOUT_RIGHT, ABOUT_TOP, ABOUT_STEP, ABOUT_SIZE, ABOUT_LINES = 22.8385, 359.52, 656.2369, 10.4998, 8.0406, 13
KPI_TITLE = (390.779, 672.223, 6.937338929)
KPI_TITLE_RIGHT = 562.
KPI_TITLE_MINIMUM = 5.5
# MS and T captions that must never appear on a composite unless its own
# sleeves use the same words.
FOREIGN_TEXT = ("Alpha World Enhanced Index", "Alpha World T Enhanced Index", "Greater China", "China & Hong Kong",
                "US Technology", "S&P500", "130% LONG", "70% SHORT", "150% LONG", "150% SHORT")


class _Font:
    """An embedded template font: its reference, how to encode text and its advance widths."""

    def __init__(self, reference, codes, widths, encode):
        self.reference, self.codes, self._widths, self._encode = reference, codes, widths, encode

    def covers(self, text: str) -> bool:
        return all(char in self.codes for char in text)

    def width(self, text: str, size: float) -> float:
        return sum(self._widths[char] for char in text) * size / 1000

    def encode(self, text: str) -> str:
        return self._encode(text)


def _embedded(reference) -> _Font:
    from .alpha_world_ms_copy import _EmbeddedFont
    font = _EmbeddedFont(reference)
    return _Font(reference, set(font.codes), {c: font.widths.get(code, font.default_width)
                                               for c, code in font.codes.items()}, font.encode)


def _ansi(reference) -> _Font:
    font = reference.get_object()
    first = int(font["/FirstChar"])
    widths = {chr(first + i): float(w) for i, w in enumerate(font["/Widths"]) if float(w) > 0}
    printable = {c: w for c, w in widths.items() if c.isprintable()}
    return _Font(reference, set(printable), printable, lambda text: text.encode("cp1252").hex())


def _fonts(style):
    """Georgia Bold and Arial Bold from the approved MS sections, and the full Georgia already used for the
    MS disclaimer. MS and T draw their own names with these same embedded fonts."""
    from .alpha_world_ms_copy import FONT_SHA256
    from .alpha_world_t_sections import find_font
    raw = (Path(__file__).resolve().parent / "assets/alpha_world_ms_disclaimer_font.pdf").read_bytes()
    if hashlib.sha256(raw).hexdigest() != FONT_SHA256:
        raise ValueError("MS disclaimer font authority differs")
    georgia = PdfReader(io.BytesIO(raw)).pages[0]["/Resources"]["/Font"]["/MSDisclaimerGeorgia"]
    return {"title": _embedded(find_font(style, "Georgia-Bold").reference), "regular": _embedded(georgia),
            "kpi": _ansi(find_font(style, "Arial-BoldMT").reference)}


def _fit(width_at, size: float, available: float, minimum: float, what: str) -> float:
    if width_at(size) > available:
        size = max(minimum, math.floor(size * available / width_at(size) * 100) / 100)
    if width_at(size) > available:
        raise ValueError(f"The composite's name is too long for the factsheet {what}")
    return size


def title_run(name: str, fonts, style: str) -> dict:
    """The title in the design's Georgia Bold, or synthetic bold Georgia where the subset lacks a letter."""
    x, y, size = DESIGNS[style]["title"]
    real = fonts["title"].covers(name)
    font = fonts["title"] if real else fonts["regular"]
    if not font.covers(name):
        raise ValueError("The composite's name uses a character the factsheet fonts do not carry")
    spacing = 0. if real else SYNTHETIC_BOLD_SPACING

    def width_at(s):
        return font.width(name, s) + spacing * s * len(name)

    size = _fit(width_at, size, TITLE_RIGHT - x, TITLE_MINIMUM, "title")
    return {"font": "title" if real else "regular", "text": name, "x": x, "y": y, "size": size,
            "synthetic_bold": not real, "width": width_at(size)}


def kpi_title_run(name: str, fonts) -> dict:
    x, y, size = KPI_TITLE
    if not fonts["kpi"].covers(name):
        raise ValueError("The composite's name uses a character the factsheet fonts do not carry")
    size = _fit(lambda s: fonts["kpi"].width(name, s), size, KPI_TITLE_RIGHT - x, KPI_TITLE_MINIMUM, "KPI panel")
    return {"font": "kpi", "text": name, "x": x, "y": y, "size": size}


def about_lines(description: str) -> list[list[str]]:
    """Words per line, filled to the MS About column; at most its 13 lines."""
    width = ABOUT_RIGHT - ABOUT_X
    lines, line = [], []
    for word in description.split():
        if stringWidth(word, "Helvetica", ABOUT_SIZE) > width:
            raise ValueError("A word in the composite's description is too long for the About column")
        if line and stringWidth(" ".join(line + [word]), "Helvetica", ABOUT_SIZE) > width:
            lines.append(line)
            line = []
        line.append(word)
    if line:
        lines.append(line)
    if len(lines) > ABOUT_LINES:
        raise ValueError("The composite's description does not fit the About column")
    return lines


def _draw_about(c, description: str) -> None:
    """Justified like the MS About text; the last line is left aligned."""
    lines = about_lines(description)
    c.setFillColor(black)
    c.setFont("Helvetica", ABOUT_SIZE)
    for number, words in enumerate(lines):
        y = ABOUT_TOP - number * ABOUT_STEP
        last = number == len(lines) - 1
        if last or len(words) == 1:
            c.drawString(ABOUT_X, y, " ".join(words))
            continue
        widths = [stringWidth(w, "Helvetica", ABOUT_SIZE) for w in words]
        gap = (ABOUT_RIGHT - ABOUT_X - sum(widths)) / (len(words) - 1)
        x = ABOUT_X
        for word, width in zip(words, widths):
            c.drawString(x, y, word)
            x += width + gap


def _native_text(page, fonts, runs, color_space) -> None:
    """Draw runs with embedded template fonts, as the T factsheet draws its KPI title."""
    overlay = PageObject.create_blank_page(width=float(page.mediabox.width), height=float(page.mediabox.height))
    resources = {NameObject(f"/C{key.capitalize()}"): fonts[key].reference for key in {r["font"] for r in runs}}
    overlay[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject(resources),
        NameObject("/ColorSpace"): DictionaryObject({NameObject("/CKpiColor"): color_space})})
    from .alpha_world_t_sections import KPI_TITLE_COLOR
    parts = []
    for run in runs:
        font = fonts[run["font"]]
        if run["font"] == "kpi":
            color = "/CKpiColor cs " + " ".join(format(v, ".4f") for v in KPI_TITLE_COLOR) + " scn"
        else:
            rgb = " ".join(format(v, ".4f") for v in TITLE_COLOR)
            color = f"{rgb} rg {rgb} RG"
        mode = ""
        if run.get("synthetic_bold"):
            mode = (f"2 Tr {SYNTHETIC_BOLD_STROKE * run['size']:.4f} w "
                    f"{SYNTHETIC_BOLD_SPACING * run['size']:.4f} Tc ")
        parts.append(f"q {color} BT /C{run['font'].capitalize()} {run['size']:.4f} Tf {mode}"
                     f"1 0 0 1 {run['x']} {run['y']} Tm <{font.encode(run['text'])}> Tj ET Q")
    content = DecodedStreamObject()
    content.set_data("\n".join(parts).encode("ascii"))
    overlay[NameObject("/Contents")] = content
    page.merge_page(overlay)


def _style_without_about(approved):
    """The approved MS sections with the MS About paragraphs removed."""
    style = PdfWriter(clone_from=approved)
    removed = _strip_variables(style.pages[0], (ABOUT_TEXT,))
    if not removed["text"]:
        raise ValueError("The approved MS sections no longer carry the About text this design replaces")
    return style


def _text(path) -> list[str]:
    return [" ".join((page.extract_text() or "").split()) for page in PdfReader(str(path)).pages]


def validate(path, profile: ReportProfile, inputs, header_values) -> dict:
    """Read the written PDF back: the composite's identity, numbers and tree are there, MS/T's are not."""
    from .monthly_artifacts import MonthlyArtifactError
    pages = _text(path)
    if len(pages) != 2:
        raise MonthlyArtifactError("The composite factsheet must have two pages")
    first, second = pages

    def require(ok, message):
        if not ok:
            raise MonthlyArtifactError(f"Composite factsheet check failed: {message}")

    require(first.count(profile.display_name) >= 2, "title and KPI panel name")
    require(" ".join(profile.description.split()) in first, "About text")
    for _, value in header_values:
        require(value in first, f"header value {value}")
    for field in ("annualized_return", "annualized_volatility", "maximum_drawdown", "total_return"):
        value = inputs.chart_metrics[field]
        require(value is not None and f"{value:+.2%}" in first, f"KPI {field}")
    period = date.fromisoformat(inputs.period_end)
    for text in pages:
        require(f"{period:%B} {period.day} {period.year}" in text, "date header")
    shapes = factsheet_tree.layout(profile.composition)
    for text in shapes.texts:
        require(text.value in second, f"tree text {text.value}")
    own = " ".join([profile.display_name, profile.description]
                   + [str(item["name"]) for group in profile.composition["groups"] for item in group["items"]])
    for foreign in FOREIGN_TEXT:
        if foreign not in own:
            require(all(foreign not in text for text in pages), f"leftover design text '{foreign}'")
    require("Disclaimer" in second and "Sprout Asset" in second, "design disclaimer")
    return {"status": "pass", "title_and_kpi_name": True, "about_text": True, "header_values": True,
            "kpi_values": True, "tree_texts": len(shapes.texts), "foreign_text_absent": True}


def render(snapshot, *, template_path, output_path, design_model_path=None):
    """Render a composite's factsheet in its profile's design.

    MS: design_model_path is the MS completed factsheet (the base) and
    template_path the MS template, recorded as supporting evidence.
    T: template_path is the T owner template.
    """
    from .monthly_artifacts import (FactsheetInputs, MonthlyArtifactError, ALPHA_WORLD_FACTSHEET_LAYOUT,
                                    _draw_alpha_world_ms_date_header, _normalize_nav, _trailing_twelve_month_return)
    try:
        profile = snapshot_profile(snapshot)
    except ValueError as error:
        raise MonthlyArtifactError(str(error)) from error
    if profile is None or profile.built_in:
        raise MonthlyArtifactError("Only a composite with an Alpha World report profile uses this factsheet")
    if snapshot.get("quality_status") == "fail":
        raise MonthlyArtifactError("A failed composite snapshot cannot be rendered")
    design = DESIGNS[profile.style]
    if profile.style == "alpha-world-ms":
        if design_model_path is None:
            raise MonthlyArtifactError("The MS design needs its completed factsheet")
        base_path, supporting = Path(design_model_path), Path(template_path)
    else:
        base_path, supporting = Path(template_path), None
        # The month-end run downloads the completed example to its own file;
        # for T it is the same PDF as the owner template, so compare contents.
        if design_model_path is not None and Path(design_model_path).resolve() != base_path.resolve() \
                and Path(design_model_path).read_bytes() != base_path.read_bytes():
            raise MonthlyArtifactError("The T design has one source, its owner template")
    original = base_path.read_bytes()
    if hashlib.sha256(original).hexdigest() != design["authority"]:
        raise MonthlyArtifactError(f"The {profile.style} source is not its approved design authority")
    supporting_sha256 = hashlib.sha256(supporting.read_bytes()).hexdigest() if supporting else design["authority"]
    destination = Path(output_path)
    sources = {base_path.resolve()} | ({supporting.resolve()} if supporting else set())
    if destination.resolve() in sources or destination.exists():
        raise MonthlyArtifactError("The factsheet output must be a new file, not a source or existing report")
    try:
        metrics, daily, evidence = analyze(snapshot)
    except (ValueError, KeyError, TypeError) as error:
        raise MonthlyArtifactError(str(error)) from error
    if not all(math.isfinite(v) for v in metrics.values() if v is not None):
        raise MonthlyArtifactError("The composite's metrics are not finite")
    normalized = _normalize_nav(snapshot["performance"]["series"])
    sleeves = sum(len(group["items"]) for group in profile.composition["groups"])
    inputs = FactsheetInputs(
        subject_id=profile.subject_id, reporting_currency="HKD", is_composite=True,
        history_start_date=snapshot["period_start"], period_end=snapshot["period_end"],
        data_as_of=snapshot["data_as_of"], display_date=snapshot["period_end"], nav=normalized[-1]["nav"],
        positions=sleeves, gross_market_value=0., top_holdings=(), sectors=(), history=(),
        normalized_nav=normalized,
        trailing_twelve_month_return=_trailing_twelve_month_return(normalized, snapshot["period_end"]),
        chart_metrics=metrics, chart_daily_returns=daily, chart_evidence=evidence)
    inception = date.fromisoformat(profile.inception)
    header_values = ((T_HEADER_VALUES[0][0], f"{inception:%B %Y}"), (T_HEADER_VALUES[1][0], "HKD"),
                     (T_HEADER_VALUES[2][0], T_HEADER_VALUES[2][1]), (T_HEADER_VALUES[3][0], design["risk_profile"]))
    source = PdfReader(io.BytesIO(original))
    if len(source.pages) != 2 or source.get_fields():
        raise MonthlyArtifactError("The design source must have two static pages")
    from .alpha_world_t_sections import STYLE_SHA256, load_style, merge_static_sections, strategy_color_space
    approved = load_style()
    style = _style_without_about(approved)
    try:
        fonts = _fonts(approved)
        runs = [title_run(profile.display_name, fonts, profile.style), kpi_title_run(profile.display_name, fonts)]
        about_lines(profile.description)
        factsheet_tree.layout(profile.composition)
    except ValueError as error:
        raise MonthlyArtifactError(str(error)) from error
    regions = (REGIONS[0] + (TITLE_REGION,), REGIONS[1])
    writer, removed = PdfWriter(clone_from=source), []
    for number, page in enumerate(writer.pages):
        removed.append(_strip_variables(page, regions[number]))
        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=(float(page.mediabox.width), float(page.mediabox.height)))
        c.setFillColor(white)
        for region in regions[number]:
            c.rect(*region, fill=1, stroke=0)
        c.save()
        buffer.seek(0)
        page.merge_page(PdfReader(buffer).pages[0])
        merge_static_sections(page, style, number)
        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=(float(page.mediabox.width), float(page.mediabox.height)))
        period = date.fromisoformat(inputs.period_end)
        _draw_alpha_world_ms_date_header(c, f"{period:%B} {period.day} {period.year}",
                                         ALPHA_WORLD_FACTSHEET_LAYOUT[f"page_{number + 1}"]["date_y"])
        if number == 0:
            _page_one(c, snapshot, inputs, header_values)
            _draw_about(c, profile.description)
        else:
            factsheet_tree.draw(c, profile.composition)
        _footer(c, number + 1, synthetic=snapshot.get("synthetic_data") is True)
        c.save()
        buffer.seek(0)
        page.merge_page(PdfReader(buffer).pages[0])
        if number == 0:
            _native_text(page, fonts, runs, strategy_color_space(approved))
            if profile.style == "alpha-world-ms":
                from .alpha_world_ms_subtitle import apply
                apply(page)
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    family = f"{profile.style}-composite"
    writer.add_metadata({
        "/Title": f"{profile.display_name} Factsheet - {inputs.period_end}",
        "/Producer": "IBKR Portfolio Platform composite factsheet renderer",
        "/LayoutFamily": family, "/ChartStyleVersion": LAYOUT_VERSION,
        "/LayoutAuthoritySHA256": design["authority"], "/CompositeMSStyleSHA256": STYLE_SHA256,
        "/CompositeMethodologyIdentity": json.dumps(methodology_identity(profile), sort_keys=True),
        "/CompositeCalculationEvidence": json.dumps(evidence, sort_keys=True, allow_nan=False),
        "/CompositeMetrics": json.dumps(metrics, sort_keys=True, allow_nan=False),
        "/CompositeSnapshotSHA256": hashlib.sha256(canonical).hexdigest(), "/PublicationAllowed": "false",
        "/SyntheticData": str(snapshot.get("synthetic_data") is True).lower()})
    destination.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with destination.open("xb") as stream:
            created = True
            writer.write(stream)
        validation = validate(destination, profile, inputs, header_values)
    except Exception:
        if created:
            destination.unlink(missing_ok=True)
        raise
    return {"output_path": str(destination), "pages": 2, "layout_version": LAYOUT_VERSION, "layout_family": family,
            "report_style": profile.style, "display_name": profile.display_name,
            "source_template_sha256": design["authority"], "supporting_template_sha256": supporting_sha256,
            "completed_example_sha256": design["authority"],
            "period_end": inputs.period_end, "data_as_of": inputs.data_as_of, "reporting_currency": "HKD",
            "chart_calculation": {"metrics": metrics, **evidence}, "variable_content_removed": removed,
            "title_font": "synthetic-bold-georgia" if runs[0]["synthetic_bold"] else "georgia-bold",
            "pdf_accuracy_validation": validation, "publication_allowed": False,
            "release_scope": "internal_shareholder",
            "quality_status": "coverage_warning" if evidence["excluded_intervals"] else "pass",
            "publication_blockers": list(snapshot.get("publication_blockers") or [])}
