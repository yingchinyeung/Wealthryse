from __future__ import annotations

import io
import hashlib
import json
import math
import random
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream
from reportlab.lib.colors import HexColor, black, white
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas


class MonthlyArtifactError(RuntimeError):
    pass


NAVY = HexColor("#121925")
ROW_ALT = HexColor("#edf1f5")
GRID = HexColor("#cbd3dc")
CHART_GRID = HexColor("#e8e8e8")
CHART_LEGEND_BORDER = HexColor("#d9d9d9")
BLUE = HexColor("#2f62de")
MUTED = HexColor("#5d6878")
TOTAL_ROW = HexColor("#dfe3e8")
FACTSHEET_LAYOUT_VERSION = "approved-august-2026-v9-full-portfolio-history"
SUBJECT_FACTSHEET_LAYOUT_VERSION = "subject-completed-factsheet-model-v8-full-portfolio-history"
ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION = (
    "subject-completed-factsheet-model-v14r16-alpha-world-ms-owner-disclaimer"
)
FACTSHEET_RENDER_CONTRACT_VERSION = "factsheet-subject-template-and-completed-model-v1"
FACTSHEET_REFERENCE_SERIF_FONT = "Times-Bold"
FACTSHEET_REFERENCE_SERIF_SIZE = 10.0
ALPHA_WORLD_SUBJECT_IDS = frozenset({"alpha-world-ms", "alpha-world-t"})
ALPHA_WORLD_MS_FACTSHEET_AUTHORITY_SHA256 = (
    "f12d94ec0f18a757263d6956789c1ae4718b52362d77abd126ebc813ff464909"
)

# Coordinates measured from the owner-supplied Alpha World template and the
# completed Alpha World factsheet.  Alpha World deliberately has a different
# two-page design from account factsheets and must never receive the generic
# holdings, sector-allocation, or historical-performance overlays.
ALPHA_WORLD_FACTSHEET_LAYOUT = {
    "page_1": {
        # These masks deliberately extend beyond the variable glyph bounds.
        # The completed example is the immutable design authority, but none of
        # its month-specific values may remain visible beneath the governed
        # replacement text.
        "date_box": (486.0, 782.0, 104.0, 48.0),
        "date_center_x": 540.0,
        "date_y": 792.0,
        # Preserve the source model's BASE CURRENCY caption and replace only
        # its value. Redrawing the caption caused a subtle double-weighted
        # overprint in earlier output even though it was hard to see at normal
        # zoom.
        "currency_value_box": (145.0, 718.0, 100.0, 16.3),
        "currency_x": 154.7,
        "currency_y": 724.8,
        # The authority's KPI rows use an exact 15-point rhythm. Mask only
        # the old rasterized values, preserving the source labels, dividers,
        # column geometry and whitespace instead of reconstructing the table.
        "metric_value_right": 558.0,
        "metric_baselines": (650.5, 635.5, 620.5, 605.5, 590.5, 575.5, 560.5, 545.5),
        "metric_value_font_size": 7.0,
        "metric_mask_x": 516.0,
        "metric_mask_width": 46.0,
        "metric_mask_height": 10.5,
        "risk_free_mask": (438.0, 529.0, 124.0, 10.0),
        "risk_free_y": 532.0,
        # The reporting-period caption crosses the authority's section rule.
        # Clear both together, then reconstruct the single measured rule.
        # The immutable authority's "Chart" ends at x=202.668. Never cover
        # its last glyph while clearing the adjacent variable date caption.
        "period_box": (205.0, 485.0, 370.0, 22.0),
        # The authority starts its caption at x=210.33 and ends at x=310.54.
        # The midpoint is therefore 260.44; this also leaves the measured gap
        # before the rule that begins at x=321.20.
        "period_center_x": 260.44,
        "period_y": 494.0,
        "period_rule": (321.2, 497.25, 251.5, 0.75),
        # Plot areas measured from the completed August 2026 authority. The
        # extra left and vertical room keeps axes and labels in the same visual
        # geometry as the supplied model instead of shrinking them inward.
        "capital_chart": (42.0, 350.0, 252.0, 124.0),
        "capital_chart_clear": (25.0, 320.0, 285.0, 160.0),
        "rebased_chart": (310.0, 350.0, 251.0, 124.0),
        "rebased_chart_clear": (310.0, 320.0, 265.0, 160.0),
        "distribution_chart": (80.0, 90.0, 463.0, 153.0),
        # Clear every legacy raster/chart title, not just the plot rectangle.
        # The authority contains older embedded charts beneath the visible one.
        "distribution_chart_clear": (70.0, 70.0, 500.0, 205.0),
    },
    "page_2": {
        "date_box": (486.0, 798.0, 104.0, 40.0),
        "date_center_x": 540.0,
        "date_y": 809.0,
    },
}

ALPHA_WORLD_MS_ALLOCATION_LAYOUT = {
    # Erase the whole inherited diagram, including its oversize right edge,
    # without touching the Asset Allocation heading or the Disclaimer below.
    "clear": (95.0, 607.0, 410.0, 148.0),
    "child_x": (99.0, 199.5, 306.0, 406.5),
    "child_y": 612.0,
    "child_width": 91.0,
    "child_height": 55.0,
    "parent_y": 710.0,
    "parent_width": 140.0,
    "parent_height": 41.0,
    "parent_caption_y": 726.2,
    "heading_y": 649.0,
    "caption_center_y": 627.5,
    "caption_line_step": 11.0,
    "branch_label_y": 687.0,
    "branch_label_offset": 35.0,
}

ALPHA_WORLD_CHART_STYLE = {
    "line_title_size": 6.0,
    "line_title_offset": 2.0,
    "distribution_title_size": 7.0,
    "distribution_title_offset": 4.0,
    "distribution_legend_width": 60.0,
    "distribution_legend_height": 38.0,
    "distribution_legend_row_step": 7.0,
    "distribution_legend_font_size": 4.5,
    # Measured from the owner-supplied August 2026 design authority. Forty
    # narrow bars are part of the approved visual language; using a smaller
    # bin count materially changes the chart even when the data are correct.
    "distribution_bins": 40,
    "distribution_bar_gap": 0.4,
}

# Owner-supplied first-page screenshots, 7 October 2026. These are presentation
# rules only: dates, values, simulation samples and reporting currency remain
# snapshot-driven. Restrict the new chart contract to MS until T is reviewed.
ALPHA_WORLD_MS_CHART_STYLE = {
    "capital_initial": 100000.0,
    "axis_color": "#3b4656",
    "tick_color": "#333333",
    "label_color": "#777777",
    "baseline_color": "#b0b0b0",
    "bar_color": "#4c5663",
    "normal_color": "#737373",
    "mean_color": "#666666",
    "historical_color": "#121925",
    "tick_length": 2.0,
    "rebased_legend_width": 50.0,
    "axis_margin": 0.05,
    "tick_font_size": 5.5,
    "legend_font_size": 5.2,
}

REGION_DISPLAY_ORDER = {
    "United States": 10,
    "North America": 20,
    "China": 30,
    "Hong Kong": 40,
    "Taiwan": 50,
    "Asia Pacific": 60,
    "Europe": 70,
    "Latin America": 80,
    "Middle East & Africa": 90,
    "Other": 100,
}

# This is the measured overlay contract from the owner-approved August 2026
# factsheet.  The model may select the reporting period and draft narrative,
# but it never supplies PDF coordinates, sizes, or fonts.
APPROVED_FACTSHEET_LAYOUT = {
    "page_1": {
        "date_center_x_from_right": 54.30,
        "date_y": 783.36,
        "top_5_heading_y": 625.00,
        "top_5_table_top": 616.88,
        "sector_heading_y": 537.00,
        "sector_table_top": 519.28,
        "history_heading_y": 269.03,
        "history_table_top": 251.28,
        "history_reference_body_rows": 21,
        "history_header_height": 20.40,
        "history_preferred_body_height": 8.60,
        "history_min_body_height": 6.80,
        "history_table_top_max": 368.00,
        "history_bottom_anchor": 50.28,
        "history_heading_gap": 17.75,
        "return_center_x_from_right": 147.50,
        # Match the approved completed-factsheet return panel. These baselines
        # are deliberately lower than the original generated overlay so the
        # return and period-end date retain the reference panel's vertical
        # rhythm rather than crowding their labels.
        "return_y": 473.00,
        "period_date_y": 270.96,
    },
    "page_2": {
        "date_center_x_from_right": 61.35,
        "date_y": 823.04,
        "title_y": 768.50,
        "chart_x": 77.20,
        "chart_y": 594.88,
        "chart_width": 413.20,
        "chart_height": 146.40,
    },
}


FACTSHEET_COMPANY_NAMES = {
    "AAPL": "Apple Inc.",
    "MU": "Micron Technology, Inc.",
    "IONQ": "IonQ, Inc.",
    "HL": "Hecla Mining Company",
    "ORCL": "Oracle Corporation",
}


def _classification_key(value: Any) -> str:
    return " ".join(
        _ascii_label(value).replace("\ufffd", "-").replace("&", " and ").lower().split()
    )


FACTSHEET_ENTERTAINMENT_SYMBOLS = {"PSKY", "VSNT"}


def _factsheet_classification(
    symbol: str, industry: Any, provider_sector: Any = None
) -> tuple[str, str, str]:
    """Map provisional provider labels into the owner-approved factsheet taxonomy."""

    key = _classification_key(industry)
    if key in {
        "consumer electronics",
        "computer hardware",
        "communication equipment",
        "electronic components",
    }:
        return "United States", "Information Technology", "Technology Hardware & Equipment"
    if key in {"semiconductors", "semiconductor equipment and materials"}:
        return (
            "United States",
            "Information Technology",
            "Semiconductors & Semiconductor Equipment",
        )
    if key in {
        "software",
        "software-infrastructure",
        "software-application",
        "information technology services",
        "packaged software",
        "internet software and services",
    }:
        return "United States", "Information Technology", "Software & Services"
    if key in {
        "medical care facilities",
        "medical devices",
        "medical instruments and supplies",
        "medical distribution",
        "diagnostics and research",
        "health information services",
    }:
        return "United States", "Health Care", "Health Care Equipment & Services"
    if key in {
        "healthcare plans",
        "biotechnology",
        "drug manufacturers-specialty and generic",
        "drug manufacturers-general",
        "pharmaceutical retailers",
    }:
        return (
            "United States",
            "Health Care",
            "Pharmaceuticals, Biotechnology & Life Sciences",
        )
    if key == "other precious metals and mining":
        return "United States", "Materials", "Metals & Mining"
    if key == "gold":
        return "United States", "Materials", "Materials"
    if key in {
        "specialty industrial machinery",
        "building products and equipment",
        "engineering and construction",
        "conglomerates",
        "industrial distribution",
    }:
        return "United States", "Industrials", "Capital Goods"
    if key == "aerospace and defense":
        return "United States", "Industrials", "Aerospace & Defense"
    # Video-game publishers sit in Communication Services (Media &
    # Entertainment) under GICS, as in the provider data the deck uses.
    if key in {
        "entertainment",
        "electronic gaming and multimedia",
        "internet content and information",
        "broadcasting",
        "advertising agencies",
        "telecom services",
    }:
        sub_sector = (
            "Entertainment"
            if symbol.upper() in FACTSHEET_ENTERTAINMENT_SYMBOLS
            else "Media & Entertainment"
        )
        return "United States", "Telecommunication Services", sub_sector
    if key == "airports and air services":
        return "United States", "Industrials", "Transportation"
    if key in {"specialty chemicals", "chemicals"}:
        return "United States", "Materials", "Chemicals"

    # Historical Yahoo labels are less consistent than the current-month
    # labels.  Keep every month inside the owner-approved thirteen-row
    # taxonomy by using Yahoo's broad sector only when an exact industry rule
    # is unavailable.  This cannot introduce a new report row or layout.
    sector_key = _classification_key(provider_sector)
    if sector_key in {"technology", "information technology"}:
        return "United States", "Information Technology", "Software & Services"
    if sector_key in {"healthcare", "health care"}:
        return (
            "United States",
            "Health Care",
            "Pharmaceuticals, Biotechnology & Life Sciences",
        )
    if sector_key in {"basic materials", "materials"}:
        return "United States", "Materials", "Materials"
    if sector_key == "industrials":
        return "United States", "Industrials", "Capital Goods"
    if sector_key in {"communication services", "telecommunication services"}:
        return "United States", "Telecommunication Services", "Media & Entertainment"
    raise MonthlyArtifactError(
        f"Factsheet taxonomy has no approved mapping for {symbol}: {industry}"
    )


def _company_name(holding: dict[str, Any]) -> str:
    if holding.get("english_name"):
        return str(holding["english_name"])
    symbol = str(holding.get("symbol") or "").upper()
    if symbol in FACTSHEET_COMPANY_NAMES:
        return FACTSHEET_COMPANY_NAMES[symbol]
    return str(holding.get("description") or symbol).title()


def _specific_region(
    holding: dict[str, Any], classification: dict[str, Any]
) -> str:
    """Use a client-friendly listing market without guessing economic exposure."""

    if holding.get("composite_sleeve"):
        return "Multi-asset"

    # USD-valued report snapshots retain native currency as listing evidence.
    # Reporting currency must not relabel Hong Kong or Taiwan holdings as US.
    currency = str(holding.get("native_currency") or holding.get("currency") or "").strip().upper()
    symbol = str(holding.get("symbol") or classification.get("symbol") or "").upper()
    if currency == "TWD" or symbol.endswith((".TW", ".TWO")):
        return "Taiwan"
    if currency == "HKD" or symbol.endswith(".HK"):
        return "Hong Kong"
    if currency in {"CNY", "CNH"} or symbol.endswith((".SS", ".SZ")):
        return "China"
    if currency in {"USD", "CAD", "MXN"}:
        return "North America"
    return _ascii_label(classification.get("region") or "Other")


def _format_regions(regions: Iterable[str]) -> tuple[str, tuple[str, ...]]:
    """Return every represented region in a stable client-facing order."""

    normalized = {
        _ascii_label(region).strip()
        for region in regions
        if _ascii_label(region).strip()
    }
    if not normalized:
        raise MonthlyArtifactError("Sector-allocation region coverage is empty")
    ordered = tuple(
        sorted(
            normalized,
            key=lambda region: (REGION_DISPLAY_ORDER.get(region, 95), region.casefold()),
        )
    )
    return " / ".join(ordered), ordered


def factsheet_layout_version(subject_id: str) -> str:
    if subject_id == "U16088571":
        return FACTSHEET_LAYOUT_VERSION
    if subject_id == "alpha-world-ms":
        return ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION
    if subject_id == 'alpha-world-t':
        from .alpha_world_t_factsheet import LAYOUT_VERSION
        return LAYOUT_VERSION
    return SUBJECT_FACTSHEET_LAYOUT_VERSION


def factsheet_render_contract_sha256(
    *, subject_id: str, supporting_template_sha256: str, completed_example_sha256: str
) -> str:
    """Bind a generated factsheet to both owner-supplied source documents."""

    evidence = {
        "schema_version": 1,
        "subject_id": subject_id,
        "supporting_template_sha256": supporting_template_sha256,
        "completed_example_sha256": completed_example_sha256,
        "layout_version": factsheet_layout_version(subject_id),
        "render_contract_version": FACTSHEET_RENDER_CONTRACT_VERSION,
    }
    return hashlib.sha256(
        json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def validate_factsheet_design_sources(
    *, template_path: str | Path, design_model_path: str | Path
) -> dict[str, Any]:
    """Fail closed unless the template and completed model form one usable design pair."""

    template_path = Path(template_path)
    design_model_path = Path(design_model_path)
    template_bytes = template_path.read_bytes()
    design_bytes = design_model_path.read_bytes()
    template = PdfReader(io.BytesIO(template_bytes))
    design_model = PdfReader(io.BytesIO(design_bytes))
    if len(template.pages) != 2 or len(design_model.pages) != 2:
        raise MonthlyArtifactError(
            "The supporting factsheet template and completed design model must both have two pages"
        )
    for page_number, (template_page, model_page) in enumerate(
        zip(template.pages, design_model.pages, strict=True), start=1
    ):
        template_size = (
            float(template_page.mediabox.width),
            float(template_page.mediabox.height),
        )
        model_size = (
            float(model_page.mediabox.width),
            float(model_page.mediabox.height),
        )
        if any(abs(left - right) > 1.0 for left, right in zip(template_size, model_size)):
            raise MonthlyArtifactError(
                f"Factsheet template and completed design model page {page_number} have incompatible dimensions"
            )
        if len((model_page.extract_text() or "").strip()) < 20:
            raise MonthlyArtifactError(
                f"Completed factsheet design model page {page_number} has insufficient visible content"
            )
    return {
        "supporting_template_sha256": hashlib.sha256(template_bytes).hexdigest(),
        "completed_example_sha256": hashlib.sha256(design_bytes).hexdigest(),
        "render_contract_version": FACTSHEET_RENDER_CONTRACT_VERSION,
    }


@dataclass(frozen=True)
class FactsheetInputs:
    subject_id: str
    reporting_currency: str
    is_composite: bool
    history_start_date: str
    period_end: str
    data_as_of: str
    display_date: str
    nav: float
    positions: int
    gross_market_value: float
    top_holdings: tuple[dict[str, Any], ...]
    sectors: tuple[dict[str, Any], ...]
    history: tuple[dict[str, Any], ...]
    normalized_nav: tuple[dict[str, Any], ...]
    trailing_twelve_month_return: float | None
    chart_metrics: dict[str, float | None] | None = None
    chart_daily_returns: tuple[float, ...] | None = None
    chart_evidence: dict[str, Any] | None = None


def _number(value: Any, *, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise MonthlyArtifactError(f"{field} is not numeric") from error
    if not math.isfinite(result):
        raise MonthlyArtifactError(f"{field} is not finite")
    return result


def _display_date(value: str, *, short: bool = False) -> str:
    parsed = date.fromisoformat(value)
    return parsed.strftime("%d %b %Y" if short else "%d %B %Y").lstrip("0")


def _percent(value: float | None, digits: int = 2) -> str:
    return "N/A" if value is None else f"{value * 100:.{digits}f}%"


def _ascii_label(value: Any) -> str:
    return (
        str(value)
        .replace("\u2011", "-")
        .replace("\u2012", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2212", "-")
    )


def _normalize_nav(series: Iterable[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    """Chain-link a cash-flow-adjusted index (start = 100) from daily NAV.

    Each row may carry ``flow``: the net external deposit (+) or withdrawal (-)
    since the previous row. Daily returns use the same Modified Dietz formula as
    the website comparison, r = (V1 - V0 - F) / (V0 + F / 2), so deposits and
    withdrawals no longer show up as performance. ``nav`` stays the raw value.
    """
    rows = list(series)
    if not rows:
        raise MonthlyArtifactError("Performance series is empty")
    first_nav = _number(rows[0].get("nav"), field="first NAV")
    if first_nav <= 0:
        raise MonthlyArtifactError("First NAV must be positive")
    normalized = []
    prior_date = ""
    prior_nav = first_nav
    index = 100.0
    for position, row in enumerate(rows):
        observed = str(row.get("date") or "")
        date.fromisoformat(observed)
        if prior_date and observed <= prior_date:
            raise MonthlyArtifactError("Performance dates are not strictly increasing")
        nav = _number(row.get("nav"), field=f"NAV {observed}")
        if nav <= 0:
            raise MonthlyArtifactError(f"NAV {observed} must be positive")
        flow = 0.0
        if position:
            flow = _number(row.get("flow") or 0.0, field=f"external flow {observed}")
            denominator = prior_nav + 0.5 * flow
            if denominator <= 0:
                raise MonthlyArtifactError(
                    f"Cash-flow-adjusted denominator is not positive on {observed}"
                )
            daily_return = (nav - prior_nav - flow) / denominator
            if not math.isfinite(daily_return) or daily_return <= -1:
                raise MonthlyArtifactError(f"Daily return is invalid on {observed}")
            index *= 1.0 + daily_return
        normalized.append({"date": observed, "value": index, "nav": nav, "flow": flow})
        prior_date = observed
        prior_nav = nav
    return tuple(normalized)


def _trailing_twelve_month_return(
    normalized_nav: tuple[dict[str, Any], ...], period_end: str
) -> float | None:
    end = date.fromisoformat(period_end)
    try:
        target = end.replace(year=end.year - 1)
    except ValueError:
        target = end.replace(year=end.year - 1, day=28)
    # The window starts at the last close on or before the same day a year
    # earlier (a month-end that fell on a weekend or holiday uses the prior
    # close), matching the month-end rows in the factsheet's own NAV table.
    eligible = [row for row in normalized_nav if date.fromisoformat(row["date"]) <= target]
    if not eligible:
        return None
    # Use the flow-adjusted index so deposits and withdrawals are not returns.
    start_value = float(eligible[-1]["value"])
    end_value = float(normalized_nav[-1]["value"])
    return end_value / start_value - 1.0


def _normalized_nav_axis(
    values: Iterable[float], *, step: float = 10.0
) -> tuple[float, float, tuple[float, ...]]:
    """Return outward-rounded, evenly spaced ticks for a normalized NAV chart."""
    clean_values = [float(value) for value in values if math.isfinite(float(value))]
    if not clean_values:
        raise MonthlyArtifactError("Normalized NAV chart has no finite values")
    if not math.isfinite(step) or step <= 0:
        raise MonthlyArtifactError("Normalized NAV axis step must be positive")

    minimum = math.floor(min(clean_values) / step) * step
    maximum = math.ceil(max(clean_values) / step) * step
    if maximum <= minimum:
        minimum -= step
        maximum += step
    tick_count = int(round((maximum - minimum) / step))
    ticks = tuple(minimum + index * step for index in range(tick_count + 1))
    return minimum, maximum, ticks


def _factsheet_sector_rows(
    holdings: list[dict[str, Any]],
    classifications: dict[str, dict[str, Any]],
    gross_market_value: float,
    *,
    subject_id: str,
) -> tuple[dict[str, Any], ...]:
    # One row represents one economic classification.  Regions are therefore
    # aggregated from every holding in the sector/sub-sector bucket instead of
    # splitting the same sub-sector into misleading single-region rows.
    totals: dict[tuple[str, str], dict[str, Any]] = {}
    for holding in holdings:
        classification = classifications.get(str(holding.get("conid") or ""))
        if classification is None:
            raise MonthlyArtifactError(
                f"Factsheet classification is unavailable for {holding.get('symbol') or holding.get('conid')}"
            )
        symbol = str(holding.get("symbol") or classification.get("symbol") or "")
        if subject_id == "U16088571":
            region, sector, sub_sector = _factsheet_classification(
                symbol,
                classification.get("sub_sector"),
                classification.get("sector"),
            )
        else:
            region = _specific_region(holding, classification)
            sector = _ascii_label(classification.get("sector") or "Other")
            sub_sector = _ascii_label(classification.get("sub_sector") or "Other")
        key = (sector, sub_sector)
        bucket = totals.setdefault(
            key,
            {
                "sector": sector,
                "sub_sector": sub_sector,
                "regions": set(),
                "holdings": 0,
                "market_value": 0.0,
            },
        )
        bucket["regions"].add(region)
        bucket["holdings"] += 1
        bucket["market_value"] += abs(
            _number(holding.get("market_value_text"), field="holding market value")
        )
    rows = list(totals.values())
    for row in rows:
        row["region"], row["region_components"] = _format_regions(row.pop("regions"))
        row["weight"] = row["market_value"] / gross_market_value
    rows.sort(key=lambda item: item["market_value"], reverse=True)
    if subject_id != "U16088571" and len(rows) > 21:
        retained = rows[:20]
        remainder = rows[20:]
        other_value = sum(float(row["market_value"]) for row in remainder)
        other_region, other_components = _format_regions(
            region
            for row in remainder
            for region in row["region_components"]
        )
        retained.append(
            {
                "region": other_region,
                "region_components": other_components,
                "sector": "Other",
                "sub_sector": "Other classified holdings",
                "holdings": sum(int(row["holdings"]) for row in remainder),
                "market_value": other_value,
                "weight": other_value / gross_market_value,
            }
        )
        rows = retained
    return tuple(rows)


def factsheet_inputs(
    snapshot: dict[str, Any], *, reporting_currencies: Iterable[str] | None = None
) -> FactsheetInputs:
    """The validated values every account-design factsheet prints.

    ``reporting_currencies`` lets a design spec that is qualified for other
    currencies use the same builder; left out, the approved rule applies
    (HKD for Alpha World MS, USD for every other subject).
    """
    required = {
        "period_end",
        "data_as_of",
        "subject_id",
        "reporting_currency",
        "portfolio_summary",
        "performance",
        "holdings",
        "sector_allocation",
        "month_end_performance",
    }
    missing = sorted(required - snapshot.keys())
    if missing:
        raise MonthlyArtifactError(f"Snapshot is missing: {', '.join(missing)}")
    subject_id = str(snapshot["subject_id"] or "").strip()
    allowed_currencies = (
        tuple(reporting_currencies) if reporting_currencies is not None
        else ('HKD',) if subject_id == 'alpha-world-ms' else ('USD',)
    )
    if not subject_id or snapshot["reporting_currency"] not in allowed_currencies:
        raise MonthlyArtifactError(
            'Factsheet renderer requires a subject id and ' + ' or '.join(allowed_currencies) + ' reporting')
    performance_series = list(snapshot["performance"].get("series") or [])
    history_start_date = str(
        snapshot.get("period_start")
        or (performance_series[0].get("date") if performance_series else "")
    )
    date.fromisoformat(history_start_date)
    period_end = str(snapshot["period_end"])
    data_as_of = str(snapshot["data_as_of"])
    date.fromisoformat(period_end)
    date.fromisoformat(data_as_of)
    if data_as_of > period_end:
        raise MonthlyArtifactError("Snapshot data date is after the reporting period")

    normalized = _normalize_nav(performance_series)
    if normalized[-1]["date"] != data_as_of:
        raise MonthlyArtifactError("Performance series does not end on data_as_of")
    # Display enrichment must not mutate the frozen input snapshot.
    holdings = [dict(row) for row in snapshot["holdings"]["holdings"]]
    holdings.sort(
        key=lambda row: abs(_number(row.get("market_value_text"), field="holding market value")),
        reverse=True,
    )
    classifications = {
        str(row.get("conid") or ""): row
        for row in snapshot["sector_allocation"].get("classifications", [])
    }
    is_composite = (
        snapshot["sector_allocation"].get("classification_basis")
        == "composite sleeve type"
    )
    # KRX holdings (Kiwoom) carry Korean names and Yahoo labels: print the English name and the
    # Korea iStrategy sectors instead, and stop on a code that has neither.
    from .korea_securities import REGION as KOREA, KoreaSecurityError, is_krx, lookup as korea_lookup
    for holding in holdings:
        if not is_composite and is_krx(holding):
            try:
                name, sector, sub_sector = korea_lookup(holding)
            except KoreaSecurityError as error:
                raise MonthlyArtifactError(str(error)) from error
            holding["english_name"] = name
            classifications[str(holding.get("conid") or "")] = {
                "symbol": holding.get("symbol"), "region": KOREA, "sector": sector, "sub_sector": sub_sector}
    if is_composite:
        for holding in holdings:
            component_type = str(holding.get("asset_category") or "").lower()
            if component_type not in {"account", "portfolio", "benchmark", "cash"}:
                raise MonthlyArtifactError("Composite component type is not approved")
            direction = str(holding.get("direction") or "").lower()
            if direction not in {"long", "short", "cash"}:
                raise MonthlyArtifactError("Composite sleeve direction is missing")
            holding["composite_sleeve"] = True
            classifications[str(holding.get("conid") or "")] = {
                "region": "Multi-asset",
                "sector": component_type.title() + " sleeve",
                "sub_sector": direction.title() + " exposure",
            }
    for holding in holdings:
        classification = classifications.get(str(holding.get("conid") or ""))
        if classification is None:
            raise MonthlyArtifactError(
                f"Factsheet classification is unavailable for {holding.get('symbol') or holding.get('conid')}"
            )
        if subject_id == "U16088571":
            region, sector, sub_sector = _factsheet_classification(
                str(holding.get("symbol") or classification.get("symbol") or ""),
                classification.get("sub_sector"),
                classification.get("sector"),
            )
        else:
            region = _specific_region(holding, classification)
            sector = _ascii_label(classification.get("sector") or "Other")
            sub_sector = _ascii_label(classification.get("sub_sector") or "Other")
        holding["region"] = region
        holding["sector"] = sector
        holding["sub_sector"] = sub_sector
        holding["company_name"] = _company_name(holding)
    gross = _number(
        snapshot["sector_allocation"]["gross_market_value"], field="gross market value"
    )
    if gross <= 0:
        raise MonthlyArtifactError("Gross market value must be positive")
    nav = _number(
        snapshot["portfolio_summary"]["latest_nav"]["total_text"], field="latest NAV"
    )
    history = [
        {
            "date": normalized[0]["date"],
            "normalized_nav": 100.0,
            "monthly_return": None,
            "cumulative_return": 0.0,
        }
    ]
    # A standalone portfolio table starts at the portfolio's first NAV, not
    # the later intersection with benchmark dates. Comparison series retain
    # their own common-period basis and must not supply this history table.
    month_end_history = snapshot["month_end_performance"]
    if subject_id != "alpha-world-ms":
        portfolio_months = {}
        for row in normalized:
            portfolio_months[row["date"][:7]] = {
                "date": row["date"], "portfolio": row["value"]
            }
        month_end_history = [portfolio_months[key] for key in sorted(portfolio_months)]
    previous_portfolio = 100.0
    for row in month_end_history:
        portfolio = _number(row["portfolio"], field="normalized month-end portfolio")
        history.append(
            {
                "date": str(row["date"]),
                "normalized_nav": portfolio,
                "monthly_return": portfolio / previous_portfolio - 1.0,
                "cumulative_return": portfolio / 100.0 - 1.0,
            }
        )
        previous_portfolio = portfolio
    chart_metrics, chart_daily_returns, chart_evidence = None, None, None
    if subject_id == "alpha-world-ms":
        from .alpha_world_ms_analytics import analyze
        try:
            chart_metrics, chart_daily_returns, chart_evidence = analyze(snapshot)
        except ValueError as error:
            raise MonthlyArtifactError(str(error)) from error
    return FactsheetInputs(
        subject_id=subject_id,
        reporting_currency=str(snapshot["reporting_currency"]),
        is_composite=is_composite,
        history_start_date=history_start_date,
        period_end=period_end,
        data_as_of=data_as_of,
        display_date=_display_date(period_end, short=True),
        nav=nav,
        positions=int(snapshot["portfolio_summary"]["positions"]),
        gross_market_value=gross,
        top_holdings=tuple(holdings[:5]),
        sectors=_factsheet_sector_rows(
            holdings, classifications, gross, subject_id=subject_id
        ),
        history=tuple(history),
        normalized_nav=normalized,
        trailing_twelve_month_return=_trailing_twelve_month_return(normalized, period_end),
        chart_metrics=chart_metrics, chart_daily_returns=chart_daily_returns, chart_evidence=chart_evidence,
    )


def _draw_date(
    c: canvas.Canvas,
    inputs: FactsheetInputs,
    page_width: float,
    page_height: float,
    *,
    center_x_from_right: float,
    baseline_y: float,
) -> None:
    c.setFillColor(white)
    c.setFont("Times-Bold", 10.0)
    c.drawCentredString(page_width - center_x_from_right, baseline_y, inputs.display_date)


def _draw_table(
    c: canvas.Canvas,
    *,
    x: float,
    top: float,
    widths: list[float],
    rows: list[list[str]],
    header_height: float,
    body_height: float,
    font_size: float,
    aligns: list[str] | None = None,
    header_aligns: list[str] | None = None,
    bold_last_row: bool = False,
    horizontal_padding: float = 6.0,
) -> None:
    aligns = aligns or ["left"] * len(widths)
    header_aligns = header_aligns or aligns
    y = top
    for row_index, row in enumerate(rows):
        row_height = header_height if row_index == 0 else body_height
        y -= row_height
        is_last = row_index == len(rows) - 1
        c.setFillColor(
            NAVY
            if row_index == 0
            else TOTAL_ROW
            if bold_last_row and is_last
            else ROW_ALT
            if row_index % 2
            else white
        )
        c.rect(x, y, sum(widths), row_height, fill=1, stroke=0)
        cursor = x
        for column, width in enumerate(widths):
            c.setStrokeColor(GRID)
            c.setLineWidth(0.35)
            c.rect(cursor, y, width, row_height, fill=0, stroke=1)
            c.setFillColor(white if row_index == 0 else NAVY)
            font_name = (
                "Helvetica-Bold"
                if row_index == 0 or (bold_last_row and is_last)
                else "Helvetica"
            )
            c.setFont(font_name, font_size)
            values = str(row[column]).split("\n")
            available_width = width - horizontal_padding
            clipped: list[str] = []
            for value in values:
                if stringWidth(value, font_name, font_size) > available_width:
                    suffix = "..."
                    while value and stringWidth(value + suffix, font_name, font_size) > available_width:
                        value = value[:-1]
                    value = value.rstrip() + suffix
                clipped.append(value)
            line_height = font_size * 1.05
            baseline = (
                y
                + row_height / 2
                + (len(clipped) - 1) * line_height / 2
                - font_size * 0.35
            )
            cell_align = header_aligns[column] if row_index == 0 else aligns[column]
            for value in clipped:
                if cell_align == "right":
                    c.drawRightString(cursor + width - horizontal_padding / 2, baseline, value)
                elif cell_align == "center":
                    c.drawCentredString(cursor + width / 2, baseline, value)
                else:
                    c.drawString(cursor + horizontal_padding / 2, baseline, value)
                baseline -= line_height
            cursor += width


FACTSHEET_HISTORY_MONTHS_SHOWN = 36


def _history_display(inputs: FactsheetInputs) -> tuple[str, list[list[str]]]:
    """The Historical Performance heading and body rows printed on page one.

    Up to 36 month-ends every row is shown. A longer history keeps its
    inception row, then a "..." row, then the latest 36 month-ends; their
    cumulative figures still run from inception.
    """
    def row(entry: dict[str, Any]) -> list[str]:
        return [
            entry["date"],
            f"{entry['normalized_nav']:.2f}",
            "-" if entry["monthly_return"] is None else _percent(entry["monthly_return"]),
            "-" if entry["monthly_return"] is None else _percent(entry["cumulative_return"]),
        ]

    inception, months = inputs.history[0], inputs.history[1:]
    end = _display_date(inputs.period_end, short=True)
    if len(months) <= FACTSHEET_HISTORY_MONTHS_SHOWN:
        return (
            f"Historical Performance from {_display_date(inputs.history_start_date, short=True)} to {end}",
            [row(entry) for entry in inputs.history],
        )
    return (
        f"Historical Performance, last {FACTSHEET_HISTORY_MONTHS_SHOWN} months to {end}",
        [
            [f"Inception {_display_date(inception['date'], short=True)}", f"{inception['normalized_nav']:.2f}", "-", "-"],
            ["...", "", "", ""],
            *(row(entry) for entry in months[-FACTSHEET_HISTORY_MONTHS_SHOWN:]),
        ],
    )


def _historical_performance_geometry(body_rows: int) -> tuple[float, float, float]:
    """Keep the growing history table bottom-aligned and expand it upward."""
    if body_rows < 1:
        raise MonthlyArtifactError("Historical Performance table has no body rows")
    layout = APPROVED_FACTSHEET_LAYOUT["page_1"]
    reference_rows = int(layout["history_reference_body_rows"])
    preferred_height = float(layout["history_preferred_body_height"])
    minimum_height = float(layout["history_min_body_height"])
    baseline_top = float(layout["history_table_top"])
    maximum_top = float(layout["history_table_top_max"])
    bottom_anchor = float(layout["history_bottom_anchor"])
    header_height = float(layout["history_header_height"])

    added_rows = max(0, body_rows - reference_rows)
    table_top = min(maximum_top, baseline_top + added_rows * preferred_height)
    available_body_height = table_top - bottom_anchor - header_height
    body_height = min(preferred_height, available_body_height / body_rows)
    if body_height < minimum_height:
        raise MonthlyArtifactError(
            "Historical Performance table exceeds the approved two-page layout capacity"
        )
    heading_y = table_top + float(layout["history_heading_gap"])
    return heading_y, table_top, body_height


def _draw_factsheet_page_one(
    c: canvas.Canvas, inputs: FactsheetInputs, page_width: float, page_height: float
) -> None:
    layout = APPROVED_FACTSHEET_LAYOUT["page_1"]
    _draw_date(
        c,
        inputs,
        page_width,
        page_height,
        center_x_from_right=layout["date_center_x_from_right"],
        baseline_y=layout["date_y"],
    )
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 7.5)
    c.drawString(27, layout["top_5_heading_y"], "Top 5 Components" if inputs.is_composite else "Top 5 Products")
    top_rows = [["COMPANY", "REGION", "SECTOR", "SUB-SECTOR", "%"]]
    for holding in inputs.top_holdings:
        market_value = _number(holding["market_value_text"], field="holding market value")
        top_rows.append(
            [
                str(holding["company_name"]),
                str(holding["region"]),
                str(holding["sector"]),
                str(holding["sub_sector"]),
                f"{abs(market_value) / inputs.gross_market_value * 100:.2f}%",
            ]
        )
    _draw_table(
        c,
        x=27,
        top=layout["top_5_table_top"],
        widths=[112, 34, 57, 113, 23],
        rows=top_rows,
        header_height=10.4,
        body_height=8.5,
        font_size=4.7,
        aligns=["left", "left", "left", "left", "right"],
        header_aligns=["center"] * 5,
        horizontal_padding=4.0,
    )

    c.setFont("Helvetica-Bold", 7.5)
    c.drawString(27, layout["sector_heading_y"], "Component Allocation" if inputs.is_composite else "Sector Allocation")
    sector_rows = [["REGION", "SECTOR", "SUB-SECTOR", "%"]]
    for sector in inputs.sectors:
        sector_rows.append(
            [
                sector["region"],
                sector["sector"],
                sector["sub_sector"],
                f"{sector['weight'] * 100:.2f}%",
            ]
        )
    sector_rows.append(
        ["TOTAL", "", "", "100.00%"]
    )
    _draw_table(
        c,
        x=27,
        top=layout["sector_table_top"],
        widths=[65, 81, 143, 50],
        rows=sector_rows,
        header_height=10.4,
        body_height=8.55,
        font_size=4.7,
        aligns=["left", "left", "left", "right"],
        header_aligns=["center"] * 4,
        bold_last_row=True,
    )

    history_heading, history_body = _history_display(inputs)
    history_heading_y, history_table_top, body_height = _historical_performance_geometry(
        len(history_body)
    )
    c.setFont("Helvetica-Bold", 7.8)
    c.drawString(27, history_heading_y, history_heading)
    history_rows = [[
        "DATE",
        "NORMALIZED NAV",
        "PERFORMANCE VS\nPREVIOUS MONTH",
        "CUMULATIVE PERFORMANCE\nFR INITIAL DATE",
    ], *history_body]
    _draw_table(
        c,
        x=27,
        top=history_table_top,
        widths=[68, 68, 102, 101],
        rows=history_rows,
        header_height=layout["history_header_height"],
        body_height=body_height,
        font_size=5.2,
        aligns=["center", "right", "right", "right"],
        header_aligns=["center"] * 4,
    )

    c.setFillColor(NAVY)
    c.setFont(FACTSHEET_REFERENCE_SERIF_FONT, FACTSHEET_REFERENCE_SERIF_SIZE)
    c.drawCentredString(
        page_width - layout["return_center_x_from_right"],
        layout["return_y"],
        "N/A"
        if inputs.trailing_twelve_month_return is None
        else f"{inputs.trailing_twelve_month_return * 100:.2f} %",
    )
    c.setFont(FACTSHEET_REFERENCE_SERIF_FONT, FACTSHEET_REFERENCE_SERIF_SIZE)
    c.drawCentredString(
        page_width - layout["return_center_x_from_right"],
        layout["period_date_y"],
        inputs.display_date,
    )


def _account_chart_month_ticks(start: date, end: date) -> tuple[date, ...]:
    """First-of-month ticks after ``start``: every odd month, thinned for long histories.

    Up to three years keeps the approved Jan/Mar/May... cadence. Longer
    histories step to Jan/Jul, then to January only, so labels stay apart.
    """
    month_span = (end.year - start.year) * 12 + end.month - start.month
    month_step = 2 if month_span <= 36 else 6 if month_span <= 72 else 12
    # Zero-based month index; January is index 0, so odd months are even indices.
    index = start.year * 12 + start.month - 1
    index += (-index) % month_step
    if date(index // 12, index % 12 + 1, 1) <= start:
        index += month_step
    ticks: list[date] = []
    while (tick := date(index // 12, index % 12 + 1, 1)) <= end:
        ticks.append(tick)
        index += month_step
    return tuple(ticks)


def _draw_factsheet_page_two(
    c: canvas.Canvas, inputs: FactsheetInputs, page_width: float, page_height: float
) -> None:
    layout = APPROVED_FACTSHEET_LAYOUT["page_2"]
    _draw_date(
        c,
        inputs,
        page_width,
        page_height,
        center_x_from_right=layout["date_center_x_from_right"],
        baseline_y=layout["date_y"],
    )
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 10.0)
    c.drawCentredString(
        page_width / 2,
        layout["title_y"],
        f"Historical Performance ({_display_date(inputs.history_start_date, short=True)} to {_display_date(inputs.period_end, short=True)})",
    )

    _draw_account_nav_chart(
        c, inputs.normalized_nav,
        box=(layout["chart_x"], layout["chart_y"], layout["chart_width"], layout["chart_height"]),
    )


def _draw_account_nav_chart(
    c: canvas.Canvas, normalized_nav: tuple[dict[str, Any], ...], *, box: tuple[float, float, float, float]
) -> None:
    """The account design's page-two NAV line chart; also a design-spec component."""
    x0, y0, width, height = box
    values = [float(row["value"]) for row in normalized_nav]
    value_range = max(values) - min(values)
    padding = max(2.0, value_range * 0.05)
    minimum = min(values) - padding
    maximum = max(values) + padding
    if maximum <= minimum:
        minimum -= 5.0
        maximum += 5.0
    # Ten-point gridlines, widened for strongly trending histories so the
    # labels never pile up (no more than ten across the plot).
    y_step = next(
        step
        for step in (10.0, 20.0, 25.0, 50.0, 100.0, 200.0, 250.0, 500.0, math.inf)
        if (maximum - minimum) / step <= 10.0
    )
    if not math.isfinite(y_step):
        y_step = 10.0 ** math.ceil(math.log10((maximum - minimum) / 10.0))
    first_tick = math.ceil(minimum / y_step) * y_step
    last_tick = math.floor(maximum / y_step) * y_step
    y_ticks = tuple(
        first_tick + y_step * index
        for index in range(max(0, int(round((last_tick - first_tick) / y_step))) + 1)
    )
    c.setStrokeColor(GRID)
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 6.8)
    for value in y_ticks:
        y = y0 + height * (value - minimum) / (maximum - minimum)
        c.setLineWidth(0.35)
        c.setDash(2, 2)
        c.line(x0, y, x0 + width, y)
        c.setDash()
        c.drawRightString(x0 - 5, y - 2.2, f"{value:.0f}")
    start_date = date.fromisoformat(normalized_nav[0]["date"])
    end_date = date.fromisoformat(normalized_nav[-1]["date"])
    start_ordinal = start_date.toordinal()
    ordinal_span = max(1, end_date.toordinal() - start_ordinal)
    date_ticks = _account_chart_month_ticks(start_date, end_date)
    c.setFont("Helvetica", 6.5)
    for tick_date in date_ticks:
        x = x0 + width * (tick_date.toordinal() - start_ordinal) / ordinal_span
        c.setDash(2, 2)
        c.line(x, y0, x, y0 + height)
        c.setDash()
        label = str(tick_date.year) if tick_date.month == 1 else tick_date.strftime("%b")
        c.saveState()
        c.translate(x - 2, y0 - 8)
        c.rotate(-45)
        c.drawString(0, -2, label)
        c.restoreState()
    c.setStrokeColor(black)
    c.setLineWidth(0.55)
    c.rect(x0, y0, width, height, fill=0, stroke=1)
    c.saveState()
    c.translate(x0 - 29, y0 + height / 2)
    c.rotate(90)
    c.setFillColor(black)
    c.setFont("Helvetica", 7.2)
    c.drawCentredString(0, 0, "Normalized NAV")
    c.restoreState()
    c.setFont("Helvetica", 7.2)
    c.drawCentredString(x0 + width / 2, y0 - 28, "Date")

    c.setStrokeColor(HexColor("#4054ff"))
    c.setLineWidth(0.75)
    path = c.beginPath()
    for index, row in enumerate(normalized_nav):
        observed = date.fromisoformat(row["date"])
        x = x0 + width * (observed.toordinal() - start_ordinal) / ordinal_span
        y = y0 + height * (float(row["value"]) - minimum) / (maximum - minimum)
        if index == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)
    c.drawPath(path, fill=0, stroke=1)


def _alpha_world_display_date(value: str) -> str:
    return date.fromisoformat(value).strftime("%B %d %Y").replace(" 0", " ")


def _alpha_world_metric_values(snapshot: dict[str, Any]) -> dict[str, float | None]:
    if snapshot.get("subject_id") == "alpha-world-ms":
        from .alpha_world_ms_analytics import analyze
        try:
            return analyze(snapshot)[0]
        except ValueError as error:
            raise MonthlyArtifactError(str(error)) from error
    risk = snapshot.get("risk") or {}
    metrics = risk.get("metrics") or {}
    required = (
        "annualized_return",
        "annualized_volatility",
        "sharpe_ratio",
        "maximum_drawdown",
        "var_95_daily",
        "cvar_95_daily",
        "win_rate",
        "total_return",
    )
    missing = [field for field in required if field not in metrics]
    if missing:
        raise MonthlyArtifactError(
            "Alpha World factsheet risk metrics are missing: " + ", ".join(missing)
        )
    values: dict[str, float | None] = {}
    for field in required:
        raw = metrics.get(field)
        values[field] = None if raw is None else _number(raw, field=field)
    raw_rate = metrics.get("risk_free_annual_rate")
    if raw_rate is None:
        raw_rate = (risk.get("risk_free") or {}).get("annual_rate")
    values["risk_free_annual_rate"] = (
        None if raw_rate is None else _number(raw_rate, field="risk-free annual rate")
    )
    return values


def _alpha_world_percent(value: float | None, *, signed: bool = True) -> str:
    if value is None:
        return "N/A"
    return f"{value:+.2%}" if signed else f"{value:.2%}"


def _nice_numeric_axis(values: Iterable[float], *, ticks: int = 5) -> tuple[float, float, float]:
    clean = [float(value) for value in values if math.isfinite(float(value))]
    if not clean:
        raise MonthlyArtifactError("Chart has no finite values")
    low, high = min(clean), max(clean)
    if high <= low:
        padding = max(abs(low) * 0.05, 1.0)
        low -= padding
        high += padding
    rough = (high - low) / max(1, ticks - 1)
    magnitude = 10.0 ** math.floor(math.log10(rough))
    residual = rough / magnitude
    step = (1.0 if residual <= 1.0 else 2.0 if residual <= 2.0 else 5.0 if residual <= 5.0 else 10.0) * magnitude
    minimum = math.floor(low / step) * step
    maximum = math.ceil(high / step) * step
    if maximum <= minimum:
        maximum = minimum + step
    return minimum, maximum, step


def _alpha_world_month_ticks(start: date, end: date) -> tuple[date, ...]:
    """Return the authority's dense, whole-month performance-axis ticks."""
    month_span = max(1, (end.year - start.year) * 12 + end.month - start.month)
    month_step = (
        1 if month_span <= 8 else
        2 if month_span <= 20 else
        3 if month_span <= 32 else
        6 if month_span <= 50 else
        12
    )
    # Begin strictly after the partial opening month. This reproduces the
    # completed authority's Mar/May/Jul cadence for a 20 January 2025 start.
    next_month_index = start.year * 12 + start.month + 1
    while ((next_month_index - 1) % month_step) != 0:
        next_month_index += 1
    ticks: list[date] = []
    while True:
        tick_year, zero_based_month = divmod(next_month_index - 1, 12)
        observed = date(tick_year, zero_based_month + 1, 1)
        if observed > end:
            break
        ticks.append(observed)
        next_month_index += month_step
    return tuple(ticks)


def _alpha_world_ms_line_axis(values: Iterable[float]) -> tuple[float, float, float]:
    """Authority-style 5% data margins, with independent clean tick spacing.

    Do not round the plot bounds to ticks: the supplied graphs leave breathing
    room above/below the path without adding an artificial 90/150 endpoint tick.
    """
    clean = [float(value) for value in values]
    if not clean or not all(math.isfinite(value) for value in clean):
        raise MonthlyArtifactError("Alpha World chart requires finite observations")
    low, high = min(clean), max(clean)
    if high == low:
        padding = max(abs(low) * ALPHA_WORLD_MS_CHART_STYLE["axis_margin"], 1.0)
    else:
        padding = (high - low) * ALPHA_WORLD_MS_CHART_STYLE["axis_margin"]
    minimum, maximum = low - padding, high + padding
    _low, _high, step = _nice_numeric_axis([minimum, maximum], ticks=7)
    return minimum, maximum, step


def _alpha_world_ms_density_axis(peak: float) -> tuple[float, float]:
    maximum = peak * (1.0 + ALPHA_WORLD_MS_CHART_STYLE["axis_margin"])
    _low, _high, step = _nice_numeric_axis([0.0, maximum], ticks=8)
    return maximum, step


def _alpha_world_chart_date_ticks(start: date, end: date) -> tuple[tuple[date, str], ...]:
    ticks = _alpha_world_month_ticks(start, end)
    if ticks:
        return tuple((day, day.strftime("%Y-%m")) for day in ticks)
    return tuple((day, day.strftime("%d %b")) for day in dict.fromkeys((start,end)))


def _chart_numeric_label(value: float, step: float) -> str:
    if abs(value) < step * 1e-9:
        value = 0.0
    decimals=max(0, -math.floor(math.log10(step)))
    return f"{value:,.{decimals}f}"


def _draw_alpha_world_line_chart(
    c: canvas.Canvas,
    rows: tuple[dict[str, Any], ...],
    *,
    box: tuple[float, float, float, float],
    value_field: str,
    title: str,
    y_label: str,
    baseline_label: str,
    authority_style: bool = False,
    value_scale: float = 1.0,
    avoid_date_label_overlap: bool = False,
) -> None:
    x0, y0, width, height = box
    plot_left, plot_bottom = x0 + 27.0, y0 + 17.0
    plot_width, plot_height = width - 32.0, height - 29.0
    values = [float(row[value_field]) * value_scale for row in rows]
    minimum, maximum, step = (
        _alpha_world_ms_line_axis(values) if authority_style else _nice_numeric_axis(values)
    )
    tick_color = HexColor(ALPHA_WORLD_MS_CHART_STYLE["tick_color"]) if authority_style else MUTED
    axis_color = HexColor(ALPHA_WORLD_MS_CHART_STYLE["axis_color"]) if authority_style else NAVY
    label_color = HexColor(ALPHA_WORLD_MS_CHART_STYLE["label_color"]) if authority_style else MUTED
    baseline_color = HexColor(ALPHA_WORLD_MS_CHART_STYLE["baseline_color"]) if authority_style else HexColor("#89919c")
    start = date.fromisoformat(rows[0]["date"])
    end = date.fromisoformat(rows[-1]["date"])
    start_ordinal = start.toordinal()
    span = max(1, end.toordinal() - start_ordinal)

    c.setFillColor(HexColor("#fefefe"))
    c.rect(plot_left, plot_bottom, plot_width, plot_height, fill=1, stroke=0)
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", ALPHA_WORLD_CHART_STYLE["line_title_size"])
    c.drawCentredString(
        plot_left + plot_width / 2,
        plot_bottom + plot_height + ALPHA_WORLD_CHART_STYLE["line_title_offset"],
        title,
    )
    c.setStrokeColor(CHART_GRID)
    c.setFillColor(tick_color)
    tick_size=ALPHA_WORLD_MS_CHART_STYLE["tick_font_size"] if authority_style else 4.1
    c.setFont("Helvetica", tick_size)
    tick = math.ceil(minimum / step) * step if authority_style else minimum
    while tick <= maximum + step * 1e-9:
        y = plot_bottom + plot_height * (tick - minimum) / (maximum - minimum)
        c.setDash(1, 1)
        c.setLineWidth(0.25)
        c.line(plot_left, y, plot_left + plot_width, y)
        c.setDash()
        if authority_style:
            c.setStrokeColor(axis_color)
            c.setLineWidth(0.45)
            c.line(plot_left - ALPHA_WORLD_MS_CHART_STYLE["tick_length"], y, plot_left, y)
            c.setStrokeColor(CHART_GRID)
        if y_label in {"HKD", "USD", "EUR", "GBP", "CNY", "TWD"}:
            label = f"{y_label} {_chart_numeric_label(tick,step)}" if authority_style else f"{y_label} {tick:,.0f}"
        else:
            label = _chart_numeric_label(tick,step) if authority_style else f"{tick:,.0f}" if abs(tick) >= 10 else f"{tick:.1f}"
        c.drawRightString(plot_left - 3.0, y - 1.4, label)
        tick += step

    last_label_right = -math.inf
    for observed, date_label in _alpha_world_chart_date_ticks(start, end):
        x = plot_left + plot_width * (observed.toordinal() - start_ordinal) / span
        label_width = stringWidth(date_label, 'Helvetica', tick_size)
        if avoid_date_label_overlap and x-label_width/2 < last_label_right+3.0:
            continue
        last_label_right = x+label_width/2
        c.setDash(1, 1)
        c.line(x, plot_bottom, x, plot_bottom + plot_height)
        c.setDash()
        if authority_style:
            c.setStrokeColor(axis_color)
            c.setLineWidth(0.45)
            c.line(x, plot_bottom, x, plot_bottom - ALPHA_WORLD_MS_CHART_STYLE["tick_length"])
            c.setStrokeColor(CHART_GRID)
            c.setLineWidth(0.25)
        c.drawCentredString(x, plot_bottom - 7.0 if authority_style else plot_bottom - 6.0, date_label)

    c.setStrokeColor(axis_color)
    c.setLineWidth(0.45)
    c.line(plot_left, plot_bottom, plot_left, plot_bottom + plot_height)
    c.line(plot_left, plot_bottom, plot_left + plot_width, plot_bottom)
    c.setFillColor(label_color)
    c.setFont("Helvetica", 5.5 if authority_style else 4.0)
    c.drawCentredString(plot_left + plot_width / 2, y0 + 1.0, "Date")
    # Currency belongs on Capital Path tick labels, not on a second vertical
    # axis caption. Only the rebased chart has a rotated axis title in the model.
    if not authority_style or y_label not in {"HKD", "USD", "EUR", "GBP", "CNY", "TWD"}:
        c.saveState()
        c.translate(x0 + 3.0, plot_bottom + plot_height / 2)
        c.rotate(90)
        c.drawCentredString(0, 0, y_label)
        c.restoreState()

    baseline = values[0]
    baseline_y = plot_bottom + plot_height * (baseline - minimum) / (maximum - minimum)
    c.setStrokeColor(baseline_color)
    c.setLineWidth(0.35)
    c.setDash(2, 1)
    c.line(plot_left, baseline_y, plot_left + plot_width, baseline_y)
    c.setDash()
    # Render the NAV path before the white legend card so it never overprints
    # the text or makes a baseline swatch resemble a portfolio-series legend.
    c.setStrokeColor(NAVY)
    c.setLineWidth(0.9)
    path = c.beginPath()
    for index, row in enumerate(rows):
        observed = date.fromisoformat(row["date"])
        x = plot_left + plot_width * (observed.toordinal() - start_ordinal) / span
        y = plot_bottom + plot_height * (values[index] - minimum) / (maximum - minimum)
        path.moveTo(x,y) if index == 0 else path.lineTo(x,y)
    c.drawPath(path, fill=0, stroke=1)
    c.setFillColor(white)
    legend_width = (
        ALPHA_WORLD_MS_CHART_STYLE["rebased_legend_width"]
        if authority_style and baseline_label == "Base = 100" else 72.0 if authority_style else 49.0
    )
    c.setStrokeColor(CHART_LEGEND_BORDER)
    c.setLineWidth(0.25)
    c.rect(
        plot_left + 2.0,
        plot_bottom + plot_height - 8.5,
        legend_width,
        7.5,
        fill=1,
        stroke=1,
    )
    c.setStrokeColor(baseline_color)
    c.setLineWidth(0.35)
    c.setDash(2, 1)
    c.line(
        plot_left + 4.0,
        plot_bottom + plot_height - 4.8,
        plot_left + 12.0,
        plot_bottom + plot_height - 4.8,
    )
    c.setDash()
    c.setFillColor(tick_color)
    c.setFont("Helvetica", ALPHA_WORLD_MS_CHART_STYLE["legend_font_size"] if authority_style else 3.8)
    c.drawString(plot_left + 15.0, plot_bottom + plot_height - 6.1, baseline_label)



def _alpha_world_return_distribution(
    inputs: FactsheetInputs, *, paths: int = 1000
) -> tuple[list[float], float]:
    if inputs.subject_id == "alpha-world-ms":
        from .alpha_world_ms_analytics import simulate
        return simulate(inputs.chart_daily_returns, inputs.period_end, paths=paths)
    if inputs.subject_id == 'alpha-world-t' and inputs.chart_daily_returns is not None:
        from .alpha_world_t_analytics import simulate
        return simulate(inputs.chart_daily_returns, inputs.period_end, paths=paths)
    from .profiled_composite import ANALYTICS_VERSION as COMPOSITE_VERSION
    if (inputs.chart_evidence or {}).get('version') == COMPOSITE_VERSION and inputs.chart_daily_returns is not None:
        from .profiled_composite import simulate
        return simulate(inputs.chart_daily_returns, inputs.period_end, subject_id=inputs.subject_id, paths=paths)
    returns = [
        float(current["value"]) / float(previous["value"]) - 1.0
        for previous, current in zip(inputs.normalized_nav, inputs.normalized_nav[1:])
    ]
    if not returns:
        return [], 0.0
    horizon = min(252, max(20, len(returns)))
    seed = int(
        hashlib.sha256(
            f"{inputs.subject_id}|{inputs.period_end}|alpha-world-distribution-v1".encode("utf-8")
        ).hexdigest()[:16],
        16,
    )
    generator = random.Random(seed)
    simulated: list[float] = []
    for _ in range(paths):
        wealth = 1.0
        for _session in range(horizon):
            wealth *= 1.0 + returns[generator.randrange(len(returns))]
        simulated.append(wealth ** (252.0 / horizon) - 1.0 if wealth > 0 else -1.0)
    return simulated, sum(simulated) / len(simulated)


def _alpha_world_ms_skew_fit(sample: list[float]) -> tuple[float, float, float]:
    """Moment-matched skew-normal (location, scale, shape) for the plot only.

    A positive shape applied around the sample mean incorrectly puts the mode
    *right* of the mean. Match location and scale as well as skew, preserving
    the frozen simulation sample and all governed portfolio metrics.
    """
    mean = sum(sample) / len(sample)
    variance = sum((value - mean) ** 2 for value in sample) / max(1, len(sample) - 1)
    std = max(math.sqrt(variance), 1e-9)
    skew = sum(((value - mean) / std) ** 3 for value in sample) / len(sample)
    sign = -1.0 if skew < 0 else 1.0
    low, high = 0.0, 0.995
    for _ in range(64):
        delta = (low + high) / 2
        shift = delta * math.sqrt(2 / math.pi)
        candidate = ((4 - math.pi) / 2) * shift ** 3 / (1 - shift ** 2) ** 1.5
        if candidate < abs(skew):
            low = delta
        else:
            high = delta
    delta = sign * (low + high) / 2
    scale = std / math.sqrt(1 - 2 * delta * delta / math.pi)
    location = mean - scale * delta * math.sqrt(2 / math.pi)
    shape = delta / math.sqrt(1 - delta * delta)
    return location, scale, shape


def _alpha_world_ms_distribution_ticks(
    minimum: float, maximum: float, fallback_step: float,
) -> tuple[list[float], float]:
    """Quarter-return labels without changing histogram bounds or folding tails.

    A future near-flat sample can span less than two quarter ticks. Retain its
    existing fine-scale ticks in that case, rather than invent a wider range.
    Integer indices avoid accumulated floating-point drift and negative zero.
    """
    step = 0.25
    first = math.ceil(minimum / step - 1e-9)
    last = math.floor(maximum / step + 1e-9)
    if last - first < 1:
        step = fallback_step
        first = math.ceil(minimum / step - 1e-9)
        last = math.floor(maximum / step + 1e-9)
    return [index * step for index in range(first, last + 1)], step


def _draw_alpha_world_distribution(
    c: canvas.Canvas,
    inputs: FactsheetInputs,
    metrics: dict[str, float | None],
    *,
    box: tuple[float, float, float, float],
    authority_style: bool | None = None,
) -> None:
    x0, y0, width, height = box
    if authority_style is None:
        authority_style = inputs.subject_id == "alpha-world-ms"
    tick_color = HexColor(ALPHA_WORLD_MS_CHART_STYLE["tick_color"]) if authority_style else MUTED
    axis_color = HexColor(ALPHA_WORLD_MS_CHART_STYLE["axis_color"]) if authority_style else NAVY
    label_color = HexColor(ALPHA_WORLD_MS_CHART_STYLE["label_color"]) if authority_style else MUTED
    bar_color = HexColor(ALPHA_WORLD_MS_CHART_STYLE["bar_color"]) if authority_style else HexColor("#5d6673")
    normal_color = HexColor(ALPHA_WORLD_MS_CHART_STYLE["normal_color"]) if authority_style else HexColor("#838a94")
    mean_color = HexColor(ALPHA_WORLD_MS_CHART_STYLE["mean_color"]) if authority_style else NAVY
    historical_color = HexColor(ALPHA_WORLD_MS_CHART_STYLE["historical_color"]) if authority_style else black
    plot_left, plot_bottom = x0 + 34.0, y0 + 16.0
    plot_width, plot_height = width - 38.0, height - 28.0
    simulated, mean = _alpha_world_return_distribution(inputs)
    if not simulated:
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 7.0)
        c.drawCentredString(x0 + width / 2, y0 + height / 2, "Insufficient daily history")
        return
    ordered = sorted(simulated)
    lower_index = max(0, int(len(ordered) * 0.01))
    upper_index = min(len(ordered) - 1, int(len(ordered) * 0.99))
    # The distribution is a communication aid, not a published forecast. Keep
    # a small number of extreme bootstrap paths from making the approved chart
    # unreadable (especially during the first months of a new portfolio).
    # Values outside this display window are retained in the calculation and
    # accumulated into the end bins.
    minimum = ordered[0] if authority_style else max(-1.0, ordered[lower_index])
    maximum = ordered[-1] if authority_style else min(1.0, ordered[upper_index])
    historical = metrics.get("annualized_return")
    if historical is not None and (authority_style or -1.0 <= historical <= 1.0):
        minimum = min(minimum, historical)
        maximum = max(maximum, historical)
    if maximum <= minimum:
        maximum = minimum + 0.01
    minimum, maximum, x_step = _nice_numeric_axis([minimum, maximum], ticks=7)
    if not authority_style:
        minimum = max(-1.0, minimum)
        maximum = min(1.0, maximum)
    if maximum <= minimum:
        maximum = minimum + x_step
    if authority_style:
        x_ticks, label_step = _alpha_world_ms_distribution_ticks(minimum, maximum, x_step)
    else:
        x_ticks = [minimum + index * x_step
                   for index in range(math.floor((maximum - minimum) / x_step + 1e-9) + 1)]
        label_step = x_step
    bins = int(ALPHA_WORLD_CHART_STYLE["distribution_bins"])
    counts = [0] * bins
    for value in simulated:
        if authority_style and not minimum <= value <= maximum:
            raise MonthlyArtifactError("Simulation outcome outside MS histogram range")
        bounded = value if authority_style else min(max(value, minimum), maximum)
        index = min(bins - 1, int((bounded - minimum) / (maximum - minimum) * bins))
        counts[index] += 1
    bin_width_value = (maximum - minimum) / bins
    densities = [
        count / (len(simulated) * bin_width_value)
        for count in counts
    ]
    maximum_density = max(densities) or 1.0
    # Fit only the chart overlays to the frozen sample. Risk/KPI methodology
    # and the 1,000-path bootstrap remain unchanged.
    sample_variance = sum((value - mean) ** 2 for value in simulated) / max(1, len(simulated) - 1)
    degenerate = authority_style and sample_variance < 1e-20
    sample_std = max(math.sqrt(sample_variance), 1e-9)
    sample_skew = sum(((value - mean) / sample_std) ** 3 for value in simulated) / len(simulated)
    skew_alpha = max(-6.0, min(6.0, sample_skew * 2.5))
    skew_location, skew_scale, skew_shape = (
        _alpha_world_ms_skew_fit(simulated)
        if authority_style else (mean, sample_std, skew_alpha)
    )

    def normal_pdf(value: float) -> float:
        if degenerate:
            return 0.0
        z = (value - mean) / sample_std
        return math.exp(-0.5 * z * z) / (sample_std * math.sqrt(2.0 * math.pi))

    def skew_pdf(value: float) -> float:
        if degenerate:
            return 0.0
        z = (value - skew_location) / skew_scale
        normal = math.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)
        cdf = 0.5 * (1.0 + math.erf(skew_shape * z / math.sqrt(2.0)))
        return 2.0 * normal * cdf / skew_scale

    _density_minimum, density_maximum, density_step = _nice_numeric_axis(
        [0.0, maximum_density], ticks=5
    )
    if authority_style:
        # Clean half-unit density labels for a peak around 3, as in the model,
        # but adapt for each actual month's data and never clip a fitted peak.
        peak = max(maximum_density, *(max(normal_pdf(value), skew_pdf(value))
                   for value in (minimum + (maximum - minimum) * i / 120 for i in range(121))))
        density_maximum, density_step = _alpha_world_ms_density_axis(peak)

    c.setFillColor(HexColor("#fefefe"))
    c.rect(plot_left, plot_bottom, plot_width, plot_height, fill=1, stroke=0)
    c.setFillColor(NAVY)
    c.setFont(
        "Helvetica-Bold",
        ALPHA_WORLD_CHART_STYLE["distribution_title_size"],
    )
    c.drawCentredString(
        plot_left + plot_width / 2,
        plot_bottom
        + plot_height
        + ALPHA_WORLD_CHART_STYLE["distribution_title_offset"],
        # The MS simulation bootstraps source NAV returns. Interest already
        # reflected in those NAVs is retained; no extra financing deduction.
        "MC Annualized Return Distribution - After Interest",
    )
    c.setFillColor(tick_color)
    c.setFont("Helvetica", 5.5 if authority_style else 4.2)
    density_tick = 0.0
    while density_tick <= density_maximum + density_step * 1e-9:
        y = plot_bottom + plot_height * density_tick / density_maximum
        c.setStrokeColor(CHART_GRID)
        c.setLineWidth(0.25)
        c.setDash(1, 1)
        c.line(plot_left, y, plot_left + plot_width, y)
        c.setDash()
        if authority_style:
            c.setStrokeColor(axis_color)
            c.setLineWidth(0.45)
            c.line(plot_left - ALPHA_WORLD_MS_CHART_STYLE["tick_length"], y, plot_left, y)
        c.setFillColor(tick_color)
        c.drawRightString(plot_left - 3.0, y - 1.4, _chart_numeric_label(density_tick,density_step) if authority_style else f"{density_tick:.1f}")
        density_tick += density_step
    c.setStrokeColor(CHART_GRID)
    c.setLineWidth(0.3)
    c.setFillColor(bar_color)
    bar_width = plot_width / bins
    bar_gap = float(ALPHA_WORLD_CHART_STYLE["distribution_bar_gap"])
    for index, density in enumerate(densities):
        bar_height = plot_height * density / density_maximum
        c.rect(
            plot_left + index * bar_width + bar_gap / 2.0,
            plot_bottom,
            max(0.2, bar_width - bar_gap),
            bar_height,
            fill=1,
            stroke=0,
        )

    if authority_style:
        # Dotted horizontal guides remain visible over the histogram, as in
        # the supplied screenshot. They must not become a solid white mesh.
        c.setStrokeColor(CHART_GRID)
        c.setLineWidth(0.25)
        c.setDash(1, 1)
        density_tick = 0.0
        while density_tick <= density_maximum + density_step * 1e-9:
            y = plot_bottom + plot_height * density_tick / density_maximum
            c.line(plot_left, y, plot_left + plot_width, y)
            density_tick += density_step
        # Faint vertical guides connect each new return label to the plot.
        # Bounds, bins, curves and reference markers remain unchanged.
        for value in x_ticks:
            x = plot_left + plot_width * (value - minimum) / (maximum - minimum)
            c.line(x, plot_bottom, x, plot_bottom + plot_height)
        if minimum <= 0.0 <= maximum:
            zero_x = plot_left + plot_width * (-minimum) / (maximum - minimum)
            c.setStrokeColor(HexColor(ALPHA_WORLD_MS_CHART_STYLE["baseline_color"]))
            c.setLineWidth(0.4)
            c.line(zero_x, plot_bottom, zero_x, plot_bottom + plot_height)
        c.setDash()

    normal_points = []
    skew_points = []
    for index in range(121):
        value = minimum + (maximum - minimum) * index / 120.0
        x = plot_left + plot_width * index / 120.0
        normal_y = plot_bottom + plot_height * min(
            1.0, normal_pdf(value) / density_maximum
        )
        skew_y = plot_bottom + plot_height * min(
            1.0, skew_pdf(value) / density_maximum
        )
        normal_points.append((x, normal_y))
        skew_points.append((x, skew_y))

    for points, color, dashed in (
        (skew_points, black, False),
        (normal_points, normal_color, True),
    ):
        if degenerate:
            continue
        c.setStrokeColor(color)
        c.setLineWidth(0.65)
        c.setDash(2, 1) if dashed else c.setDash()
        curve = c.beginPath()
        for index, (x, y) in enumerate(points):
            curve.moveTo(x, y) if index == 0 else curve.lineTo(x, y)
        c.drawPath(curve, fill=0, stroke=1)
        c.setDash()
    c.setStrokeColor(axis_color)
    c.setLineWidth(0.45)
    c.line(plot_left, plot_bottom, plot_left, plot_bottom + plot_height)
    c.line(plot_left, plot_bottom, plot_left + plot_width, plot_bottom)
    c.setFillColor(tick_color)
    c.setFont("Helvetica", 5.5 if authority_style else 4.2)
    for value in x_ticks:
        x = plot_left + plot_width * (value - minimum) / (maximum - minimum)
        if authority_style:
            c.line(x, plot_bottom, x, plot_bottom - ALPHA_WORLD_MS_CHART_STYLE["tick_length"])
        digits=max(0,-math.floor(math.log10(label_step))-2)
        label_value = 0.0 if authority_style and abs(value) < label_step * 1e-9 else value
        c.drawCentredString(x, plot_bottom - 7.0 if authority_style else plot_bottom - 6.0, f"{label_value:.{digits}%}")
    c.setFillColor(label_color)
    c.drawCentredString(plot_left + plot_width / 2, y0 + 1.0, "Annualized Return")
    c.saveState()
    c.translate(x0 + 5.0, plot_bottom + plot_height / 2)
    c.rotate(90)
    c.drawCentredString(0, 0, "Density")
    c.restoreState()

    legend: list[tuple[str, float | None, Any, str]] = [
        (f"{len(simulated)} MC paths", None, bar_color, "bar"),
        ("Skew-normal fit: N/A" if degenerate else "Skew-normal fit", None, black, "unavailable" if degenerate else "solid"),
        ("Normal fit: N/A" if degenerate else "Normal fit", None, normal_color, "unavailable" if degenerate else "dashed"),
        (f"MC Mean: {mean:.1%}", mean, mean_color, "dashed"),
        (
            "Historical annualized return"
            if historical is None
            else f"Historical: {historical:.1%}",
            historical,
            historical_color,
            "dotted",
        ),
    ]
    # Reference markers belong behind the legend. Drawing them first prevents
    # their vertical rules from crossing the authority's white legend card.
    for _label, marker_value, marker_color, marker_style in legend:
        if marker_value is not None and minimum <= marker_value <= maximum:
            marker_x = plot_left + plot_width * (
                marker_value - minimum
            ) / (maximum - minimum)
            c.setStrokeColor(marker_color)
            c.setLineWidth(0.7)
            c.setDash(0.5, 1.2) if marker_style == "dotted" else c.setDash(2, 1)
            c.line(marker_x, plot_bottom, marker_x, plot_bottom + plot_height)
            c.setDash()
    legend_width = 87.0 if authority_style else ALPHA_WORLD_CHART_STYLE["distribution_legend_width"]
    legend_height = ALPHA_WORLD_CHART_STYLE["distribution_legend_height"]
    legend_x = plot_left + plot_width - legend_width - 2.0
    legend_top = plot_bottom + plot_height - 2.0
    c.setFillColor(white)
    c.setStrokeColor(CHART_LEGEND_BORDER)
    c.setLineWidth(0.25)
    c.rect(
        legend_x,
        legend_top - legend_height,
        legend_width,
        legend_height,
        fill=1,
        stroke=1,
    )
    legend_y = legend_top - 4.0
    c.setFont("Helvetica", 5.5 if authority_style else ALPHA_WORLD_CHART_STYLE["distribution_legend_font_size"])
    for row, (label, _value, color, marker_style) in enumerate(legend):
        y = legend_y - row * ALPHA_WORLD_CHART_STYLE["distribution_legend_row_step"]
        if marker_style == "bar":
            c.setFillColor(color)
            c.rect(legend_x + 4.0, y - 1.0, 8.0, 2.5, fill=1, stroke=0)
        elif marker_style != "unavailable":
            c.setStrokeColor(color)
            c.setLineWidth(0.7)
            if marker_style == "dashed":
                c.setDash(2, 1)
            elif marker_style == "dotted":
                c.setDash(0.5, 1.2)
            else:
                c.setDash()
            c.line(legend_x + 4.0, y, legend_x + 12.0, y)
            c.setDash()
        c.setFillColor(tick_color)
        c.drawString(legend_x + 16.0, y - 1.6, label)


def _draw_alpha_world_ms_date_header(c, display_date, baseline_y):
    """Both MS pages share a right edge and regular sans-serif date style."""
    c.setFillColor(black)
    c.setFont('Helvetica', 5.8)
    c.drawRightString(572., baseline_y+14., 'As of')
    c.setFont('Helvetica', 8.)
    c.drawRightString(572., baseline_y, display_date)


def _remove_alpha_world_ms_source_date_text(page, region):
    """Remove inherited date glyphs, so copied text matches the visible header."""
    from .alpha_world_ms_copy import _multiply, IDENTITY
    left,bottom,width,height=region
    def walk(stream,resources,transform,depth=0):
        if depth>20: raise MonthlyArtifactError('MS date-header form nesting exceeds limit')
        content=ContentStream(stream,page.pdf)
        cm,tm,stack,operations=transform,IDENTITY,[],[]
        for args,op in content.operations:
            if op==b'q': stack.append((cm,tm))
            elif op==b'Q': cm,tm=stack.pop()
            elif op==b'cm': cm=_multiply(tuple(map(float,args)),cm)
            elif op==b'BT': tm=IDENTITY
            elif op==b'Tm': tm=tuple(map(float,args))
            elif op in (b'Td',b'TD'):
                tm=_multiply((1.,0.,0.,1.,float(args[0]),float(args[1])),tm)
            elif op in (b'Tj',b'TJ'):
                matrix=_multiply(tm,cm)
                if left<=matrix[4]<=left+width and bottom<=matrix[5]<=bottom+height:
                    continue
            elif op==b'Do':
                child=resources.get('/XObject',{}).get(args[0])
                if child is not None:
                    child=child.get_object()
                    if child.get('/Subtype')=='/Form':
                        walk(child,child.get('/Resources',resources),
                             _multiply(tuple(map(float,child.get('/Matrix',IDENTITY))),cm),depth+1)
            operations.append((args,op))
        content.operations=operations
        if depth==0: page.replace_contents(content)
        else: stream.set_data(content.get_data())
    walk(page.get_contents(),page['/Resources'],IDENTITY)


def _draw_alpha_world_page_one(
    c: canvas.Canvas,
    snapshot: dict[str, Any],
    inputs: FactsheetInputs,
    page_width: float,
    page_height: float,
) -> None:
    del page_width, page_height
    layout = ALPHA_WORLD_FACTSHEET_LAYOUT["page_1"]
    metrics = inputs.chart_metrics if inputs.subject_id == "alpha-world-ms" else _alpha_world_metric_values(snapshot)
    c.setFillColor(white)
    for key in (
        "date_box",
        "currency_value_box",
        "period_box",
        "capital_chart_clear",
        "rebased_chart_clear",
        "distribution_chart_clear",
    ):
        c.rect(*layout[key], fill=1, stroke=0)
    for baseline in layout["metric_baselines"]:
        c.rect(
            layout["metric_mask_x"],
            baseline - 2.0,
            layout["metric_mask_width"],
            layout["metric_mask_height"],
            fill=1,
            stroke=0,
        )
    c.rect(*layout["risk_free_mask"], fill=1, stroke=0)

    display_date = _alpha_world_display_date(inputs.period_end)
    c.setFillColor(black)
    if inputs.subject_id == 'alpha-world-ms':
        _draw_alpha_world_ms_date_header(c, display_date, layout['date_y'])
    else:
        c.setFont("Helvetica", 5.8)
        c.drawCentredString(layout["date_center_x"], layout["date_y"] + 14.0, "As of")
        c.setFont("Times-Bold", 8.0)
        c.drawCentredString(layout["date_center_x"], layout["date_y"], display_date)
    c.setFont("Helvetica", 9.15)
    c.drawString(layout["currency_x"], layout["currency_y"], inputs.reporting_currency)

    labels = (
        ("annualized_return", True, HexColor("#2d7168")),
        ("annualized_volatility", True, HexColor("#2d7168")),
        ("sharpe_ratio", False, NAVY),
        ("maximum_drawdown", True, HexColor("#c93642")),
        ("var_95_daily", True, HexColor("#c93642")),
        ("cvar_95_daily", True, HexColor("#c93642")),
        ("win_rate", False, HexColor("#2d7168")),
        ("total_return", True, HexColor("#2d7168")),
    )
    c.setFont("Helvetica-Bold", layout["metric_value_font_size"])
    for (field, signed, color), baseline in zip(labels, layout["metric_baselines"], strict=True):
        value = metrics[field]
        text = (
            "N/A"
            if value is None
            else f"{value:.2f}"
            if field == "sharpe_ratio"
            else _alpha_world_percent(
                -abs(value) if field in {"var_95_daily", "cvar_95_daily"} else value,
                signed=signed,
            )
        )
        c.setFillColor(color)
        c.drawRightString(layout["metric_value_right"], baseline, text)
    risk_free = metrics.get("risk_free_annual_rate")
    c.setFillColor(MUTED)
    c.setFont("Helvetica-Oblique", 4.1)
    c.drawRightString(
        layout["metric_value_right"],
        layout["risk_free_y"],
        "* Risk-Free Rate (1M HIBOR, period average): unavailable" if inputs.subject_id == "alpha-world-ms" and risk_free is None else
        f"* Risk-Free Rate (1M HIBOR, period average): {risk_free:.2%}" if inputs.subject_id == "alpha-world-ms" else
        "* Risk-Free Rate (period avg.): unavailable"
        if risk_free is None
        else f"* Risk-Free Rate (period avg.): {risk_free:.2%}",
    )

    history_start = date.fromisoformat(inputs.history_start_date)
    period_end = date.fromisoformat(inputs.data_as_of if inputs.subject_id == "alpha-world-ms" else inputs.period_end)
    period_text = (
        f"({history_start.day} {history_start.strftime('%b %Y')} - "
        f"{period_end.day} {period_end.strftime('%B %Y')})"
    )
    c.setFillColor(black)
    # Helvetica at 6.5 points reproduces the authority's 100-point caption
    # width while remaining available in every production renderer.
    c.setFont("Helvetica", 6.5)
    c.drawCentredString(layout["period_center_x"], layout["period_y"], period_text)

    # Restore exactly one authority-measured section rule after the larger
    # period mask. A previous implementation left the source rule in place
    # and drew a second rule three points lower.
    c.setFillColor(HexColor("#a9a9a9"))
    c.rect(*layout["period_rule"], fill=1, stroke=0)

    _draw_alpha_world_line_chart(
        c,
        inputs.normalized_nav,
        box=layout["capital_chart"],
        value_field="value" if inputs.subject_id == "alpha-world-ms" else "nav",
        title="Capital Path",
        y_label=inputs.reporting_currency,
        baseline_label=(
            f"Initial {inputs.reporting_currency} {ALPHA_WORLD_MS_CHART_STYLE['capital_initial']:,.0f}"
            if inputs.subject_id == "alpha-world-ms"
            else f"Initial {inputs.reporting_currency} {inputs.normalized_nav[0]['nav']:,.0f}"
        ),
        authority_style=inputs.subject_id == "alpha-world-ms",
        value_scale=ALPHA_WORLD_MS_CHART_STYLE["capital_initial"] / 100.0 if inputs.subject_id == "alpha-world-ms" else 1.0,
    )
    _draw_alpha_world_line_chart(
        c,
        inputs.normalized_nav,
        box=layout["rebased_chart"],
        value_field="value",
        title="Rebased to 100",
        y_label="Index (Start = 100)",
        baseline_label="Base = 100",
        authority_style=inputs.subject_id == "alpha-world-ms",
    )
    _draw_alpha_world_distribution(
        c,
        inputs,
        metrics,
        box=layout["distribution_chart"],
    )
    if inputs.subject_id == "alpha-world-ms":
        evidence=inputs.chart_evidence
        # The locked completed design already contains HIBOR + Spread in the
        # same embedded Arial, size and baseline as Multi-Tranche. Preserve it
        # rather than covering it with a smaller substitute-font overlay.
        c.setFillColor(MUTED)
        c.setFont("Helvetica",6.0)
        # Owner requested these methodology notes stay in internal render
        # evidence, not on the factsheet. Keep exceptional data warnings below.
        gaps=evidence["excluded_intervals"]
        if gaps:
            missing=sum(len(g["missing_sessions"]) for g in gaps)
            c.setFillColor(HexColor("#9b5d13"))
            c.drawString(42,314,f"Coverage warning: {missing} missing session(s); {len(gaps)} multi-session interval(s) excluded from daily risk and MC. No prices imputed.")
        elif inputs.data_as_of != inputs.period_end:
            c.drawString(42,314,f"Reporting month-end: {inputs.period_end}; last actual valuation: {inputs.data_as_of}. No month-end prices imputed.")
        c.setFillColor(MUTED)
        c.setFont("Helvetica",6.0)
        if evidence["short_history"]:
            c.drawString(80,58,"Limited history: indicative bootstrap only, not a forecast; parameter uncertainty is not shown.")


ALPHA_WORLD_MS_ALLOCATION_LABELS = {
    "parents": ("70%", "30%"),
    "headings": ("130% LONG", "70% SHORT", "150% LONG", "150% SHORT"),
    "captions": (("China & Hong Kong", "Portfolio"), ("Hang Seng Index",),
                 ("US Technology &", "BioTechnology"), ("S&P500",)),
    "branches": ("91%", "-49%", "45%", "-45%"),
}


def _draw_alpha_world_ms_allocation(c: canvas.Canvas, labels: dict[str, Any] | None = None) -> None:
    """Owner-requested geometry only; preserve all wording and percentages."""
    layout = ALPHA_WORLD_MS_ALLOCATION_LAYOUT
    labels = labels or ALPHA_WORLD_MS_ALLOCATION_LABELS
    c.saveState()
    c.setFillColor(white)
    c.rect(*layout['clear'], fill=1, stroke=0)
    centers = [x + layout['child_width']/2 for x in layout['child_x']]
    parents = [(centers[i]+centers[i+1])/2 for i in (0, 2)]
    c.setStrokeColor(black)
    c.setLineWidth(.6)
    for index, parent in enumerate(parents):
        for child in centers[index*2:index*2+2]:
            c.line(parent, layout['parent_y'], child, layout['child_y']+layout['child_height'])
    c.setFillColor(black)
    for parent in parents:
        c.rect(parent-layout['parent_width']/2, layout['parent_y'],
            layout['parent_width'], layout['parent_height'], fill=1, stroke=0)
    for x in layout['child_x']:
        c.rect(x, layout['child_y'], layout['child_width'], layout['child_height'], fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont('Helvetica-Bold', 12.0)
    for center, label in zip(parents, labels['parents']):
        c.drawCentredString(center, layout['parent_caption_y'], label)
    for center, heading, lines in zip(centers, labels['headings'], labels['captions']):
        c.setFont('Helvetica-Bold', 10.8)
        c.drawCentredString(center, layout['heading_y'], heading)
        c.setFont('Helvetica', 9.4)
        for i, text in enumerate(lines):
            y = layout['caption_center_y'] + ((len(lines)-1)/2-i)*layout['caption_line_step']
            c.drawCentredString(center, y, text)
    c.setFillColor(black)
    c.setFont('Helvetica-Bold', 10.8)
    for i, (child, label) in enumerate(zip(centers, labels['branches'])):
        parent = parents[i//2]
        offset = -layout['branch_label_offset'] if i%2 == 0 else layout['branch_label_offset']
        c.drawCentredString((parent+child)/2+offset, layout['branch_label_y'], label)
    c.restoreState()


def _draw_alpha_world_page_two(
    c: canvas.Canvas, inputs: FactsheetInputs, page_width: float, page_height: float
) -> None:
    del page_width, page_height
    layout = ALPHA_WORLD_FACTSHEET_LAYOUT["page_2"]
    c.setFillColor(white)
    c.rect(*layout["date_box"], fill=1, stroke=0)
    c.setFillColor(black)
    if inputs.subject_id == 'alpha-world-ms':
        _draw_alpha_world_ms_date_header(c, _alpha_world_display_date(inputs.period_end), layout['date_y'])
    else:
        c.setFont("Helvetica", 5.8)
        c.drawCentredString(layout["date_center_x"], layout["date_y"] + 14.0, "As of")
        c.setFont("Times-Bold", 8.0)
        c.drawCentredString(
            layout["date_center_x"], layout["date_y"], _alpha_world_display_date(inputs.period_end)
        )
    if inputs.subject_id == 'alpha-world-ms':
        _draw_alpha_world_ms_allocation(c)
        return

    # The supplied design family places the two left-hand sleeve captions in
    # the opposite order from their coefficients.  Redraw those two captions
    # deterministically so 91% always identifies the long primary sleeve and
    # -49% always identifies the short Hang Seng sleeve.  This also lets the
    # same approved design family describe MS and T without misidentifying T's
    # primary U16088606 sleeve.
    c.setFillColor(white)
    c.rect(118.0, 681.0, 35.0, 16.0, fill=1, stroke=0)
    c.rect(232.0, 681.0, 42.0, 16.0, fill=1, stroke=0)
    c.setFillColor(black)
    c.setFont("Helvetica-Bold", 10.8)
    c.drawCentredString(135.5, 687.0, "91%")
    c.drawCentredString(253.0, 687.0, "-49%")

    for x in (99.0, 199.5):
        c.setFillColor(black)
        c.rect(x, 612.0, 91.0, 55.0, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 10.8)
    c.drawCentredString(144.5, 649.0, "130% LONG")
    primary_lines = (
        ("China & Hong Kong", "Portfolio")
        if inputs.subject_id == "alpha-world-ms"
        else ("U16088606", "Greater China")
    )
    c.setFont("Helvetica", 9.4)
    c.drawCentredString(144.5, 633.0, primary_lines[0])
    c.drawCentredString(144.5, 622.0, primary_lines[1])
    c.setFont("Helvetica-Bold", 10.8)
    c.drawCentredString(245.0, 649.0, "70% SHORT")
    c.setFont("Helvetica", 9.4)
    c.drawCentredString(245.0, 630.0, "Hang Seng Index")
    if inputs.subject_id == "alpha-world-ms":
        c.setFillColor(black)
        c.rect(399.5,612,91,55,fill=1,stroke=0)
        c.setFillColor(white)
        c.setFont("Helvetica-Bold",10.8)
        c.drawCentredString(445,649,"150% SHORT")
        c.setFont("Helvetica",9.4)
        # Owner-approved display caption; SPY remains the approved price proxy.
        c.drawCentredString(445,630,"S&P500")


def _collect_text_runs(page) -> list[tuple[str, float, float, float, str | None]]:
    runs: list[tuple[str, float, float, float, str | None]] = []

    def visitor(text, _cm, tm, font, font_size) -> None:
        value = text.strip()
        if value:
            base_font = None if font is None else str(font.get("/BaseFont", ""))
            runs.append(
                (value, float(tm[4]), float(tm[5]), float(font_size), base_font)
            )

    page.extract_text(visitor_text=visitor)
    return runs


def _require_run(
    runs: list[tuple[str, float, float, float, str | None]],
    text: str,
    *,
    x: float | None,
    y: float,
    size: float,
    font: str | None = None,
    tolerance: float = 0.15,
) -> None:
    if not any(
        value == text
        and (x is None or abs(actual_x - x) <= tolerance)
        and abs(actual_y - y) <= tolerance
        and abs(actual_size - size) <= 0.05
        and (font is None or actual_font == f"/{font}")
        for value, actual_x, actual_y, actual_size, actual_font in runs
    ):
        raise MonthlyArtifactError(
            f"Rendered factsheet violates the locked layout contract: {text!r}"
        )


def _validate_return_panel_runs(
    runs: list[tuple[str, float, float, float, str | None]], page_width: float
) -> None:
    """Reject old or overprinted return figures, not just a missing new overlay."""
    figures = [
        run for run in runs
        if page_width - 200 <= run[1] <= page_width - 80
        and 455 <= run[2] <= 495
        and re.fullmatch(r"(?:-?\d+(?:\.\d+)?\s*%|N/A)", run[0])
    ]
    if len(figures) != 1:
        raise MonthlyArtifactError(
            "Rendered factsheet return panel must contain exactly one value"
        )
    value, x, y, size, font = figures[0]
    expected_x = (
        page_width
        - APPROVED_FACTSHEET_LAYOUT["page_1"]["return_center_x_from_right"]
        - stringWidth(value, FACTSHEET_REFERENCE_SERIF_FONT, FACTSHEET_REFERENCE_SERIF_SIZE) / 2
    )
    if (
        font != f"/{FACTSHEET_REFERENCE_SERIF_FONT}"
        or abs(size - FACTSHEET_REFERENCE_SERIF_SIZE) > 0.05
        or abs(y - APPROVED_FACTSHEET_LAYOUT["page_1"]["return_y"]) > 0.15
        or abs(x - expected_x) > 0.15
    ):
        raise MonthlyArtifactError(
            "Rendered factsheet return panel does not match the approved serif typography"
        )


def validate_factsheet_return_typography(content: bytes) -> None:
    """Validate a stored PDF before it can receive a new release approval."""
    document = PdfReader(io.BytesIO(content))
    if len(document.pages) != 2:
        raise MonthlyArtifactError("Rendered factsheet must contain exactly two pages")
    page = document.pages[0]
    _validate_return_panel_runs(_collect_text_runs(page), float(page.mediabox.width))


def validate_factsheet_layout(path: str | Path, inputs: FactsheetInputs) -> None:
    """Fail closed if a rendered PDF drifts from the approved layout anchors."""
    document = PdfReader(str(path))
    if len(document.pages) != 2:
        raise MonthlyArtifactError("Rendered factsheet must contain exactly two pages")
    page_one, page_two = document.pages
    width = float(page_one.mediabox.width)
    page_one_runs = _collect_text_runs(page_one)
    page_two_runs = _collect_text_runs(page_two)
    _validate_return_panel_runs(page_one_runs, width)
    p1 = APPROVED_FACTSHEET_LAYOUT["page_1"]
    p2 = APPROVED_FACTSHEET_LAYOUT["page_2"]
    top_date_x = (
        width
        - p1["date_center_x_from_right"]
        - stringWidth(inputs.display_date, "Times-Bold", 10.0) / 2
    )
    lower_date_x = (
        width
        - p1["return_center_x_from_right"]
        - stringWidth(inputs.display_date, "Times-Bold", 10.0) / 2
    )
    page_two_date_x = (
        width
        - p2["date_center_x_from_right"]
        - stringWidth(inputs.display_date, "Times-Bold", 10.0) / 2
    )
    _require_run(page_one_runs, inputs.display_date, x=top_date_x, y=p1["date_y"], size=10.0)
    _require_run(page_one_runs, "Top 5 Components" if inputs.is_composite else "Top 5 Products", x=27, y=p1["top_5_heading_y"], size=7.5)
    _require_run(page_one_runs, "COMPANY", x=None, y=610.04, size=4.7)
    _require_run(page_one_runs, "REGION", x=None, y=610.04, size=4.7)
    _require_run(page_one_runs, "SECTOR", x=None, y=610.04, size=4.7)
    _require_run(page_one_runs, "SUB-SECTOR", x=None, y=610.04, size=4.7)
    _require_run(page_one_runs, "Component Allocation" if inputs.is_composite else "Sector Allocation", x=27, y=p1["sector_heading_y"], size=7.5)
    _require_run(page_one_runs, "REGION", x=None, y=512.44, size=4.7)
    _require_run(page_one_runs, "SECTOR", x=None, y=512.44, size=4.7)
    _require_run(page_one_runs, "SUB-SECTOR", x=None, y=512.44, size=4.7)
    sector_body_height = 8.55
    for row_index, sector in enumerate(inputs.sectors, start=1):
        expected_region, expected_components = _format_regions(
            sector.get("region_components") or ()
        )
        if sector.get("region") != expected_region:
            raise MonthlyArtifactError(
                "Sector-allocation region label does not match its holding coverage"
            )
        if tuple(sector.get("region_components") or ()) != expected_components:
            raise MonthlyArtifactError(
                "Sector-allocation region components are not deterministically ordered"
            )
        row_y = (
            p1["sector_table_top"]
            - 10.4
            - sector_body_height * row_index
            + sector_body_height / 2
            - 4.7 * 0.35
        )
        _require_run(
            page_one_runs,
            expected_region,
            x=None,
            y=row_y,
            size=4.7,
        )
    history_heading, history_body = _history_display(inputs)
    history_heading_y, history_table_top, _ = _historical_performance_geometry(
        len(history_body)
    )
    _require_run(
        page_one_runs,
        history_heading,
        x=27,
        y=history_heading_y,
        size=7.8,
    )
    history_header_y = (
        history_table_top
        - p1["history_header_height"]
        + p1["history_header_height"] / 2
        - 5.2 * 0.35
    )
    _require_run(page_one_runs, "DATE", x=None, y=history_header_y, size=5.2)
    return_text = (
        "N/A"
        if inputs.trailing_twelve_month_return is None
        else f"{inputs.trailing_twelve_month_return * 100:.2f} %"
    )
    return_x = (
        width
        - p1["return_center_x_from_right"]
        - stringWidth(
            return_text,
            FACTSHEET_REFERENCE_SERIF_FONT,
            FACTSHEET_REFERENCE_SERIF_SIZE,
        )
        / 2
    )
    _require_run(
        page_one_runs,
        return_text,
        x=return_x,
        y=p1["return_y"],
        size=FACTSHEET_REFERENCE_SERIF_SIZE,
        font=FACTSHEET_REFERENCE_SERIF_FONT,
    )
    _require_run(
        page_one_runs,
        inputs.display_date,
        x=lower_date_x,
        y=p1["period_date_y"],
        size=FACTSHEET_REFERENCE_SERIF_SIZE,
        font=FACTSHEET_REFERENCE_SERIF_FONT,
    )
    _require_run(page_two_runs, inputs.display_date, x=page_two_date_x, y=p2["date_y"], size=10.0)
    _require_run(page_two_runs, "Date", x=None, y=p2["chart_y"] - 28, size=7.2, tolerance=0.2)
    _require_run(
        page_two_runs,
        f"Historical Performance ({_display_date(inputs.history_start_date, short=True)} to {_display_date(inputs.period_end, short=True)})",
        x=None,
        y=p2["title_y"],
        size=10.0,
        tolerance=0.2,
    )


def _validate_alpha_world_source_layout(
    *,
    subject_id: str,
    template_path: str | Path,
    design_model_path: str | Path,
) -> None:
    """Require both uploaded documents to describe the Alpha World layout family."""

    if subject_id == "alpha-world-ms":
        authority_sha256 = hashlib.sha256(
            Path(design_model_path).read_bytes()
        ).hexdigest()
        if authority_sha256 != ALPHA_WORLD_MS_FACTSHEET_AUTHORITY_SHA256:
            raise MonthlyArtifactError(
                "Alpha World MS completed factsheet is not the approved August 2026 "
                "design authority"
            )

    for label, path in (
        ("template", template_path),
        ("completed factsheet", design_model_path),
    ):
        document = PdfReader(str(path))
        pages = [" ".join((page.extract_text() or "").split()) for page in document.pages]
        first = pages[0].casefold()
        second = pages[1].casefold()
        first_page_anchors = (
            "alpha world enhanced index",
            "key performance indicators",
            "daily performance chart",
            "distribution of annualized returns",
        )
        missing = [anchor for anchor in first_page_anchors if anchor not in first]
        if "asset allocation" not in second or "disclaimer" not in second:
            missing.extend(["asset allocation", "disclaimer"])
        if missing:
            raise MonthlyArtifactError(
                f"Alpha World {label} does not match the approved Alpha World factsheet family: "
                + ", ".join(sorted(set(missing)))
            )


def validate_alpha_world_factsheet_layout(
    path: str | Path | io.BytesIO, inputs: FactsheetInputs
) -> None:
    """Fail closed if Alpha World receives a generic account overlay or loses its anchors."""

    document = PdfReader(path)
    if len(document.pages) != 2:
        raise MonthlyArtifactError("Rendered Alpha World factsheet must contain exactly two pages")
    pages = [" ".join((page.extract_text() or "").split()) for page in document.pages]
    first, second = pages
    first_folded, second_folded = first.casefold(), second.casefold()
    distribution_title="MC Annualized Return Distribution - After Interest"
    for anchor in (
        "alpha world enhanced index",
        "key performance indicators",
        "daily performance chart",
        "distribution of annualized returns",
        "capital path",
        "rebased to 100",
        distribution_title.casefold(),
    ):
        if anchor not in first_folded:
            raise MonthlyArtifactError(
                f"Rendered Alpha World factsheet is missing the approved layout anchor: {anchor}"
            )
    for anchor in ("asset allocation", "disclaimer"):
        if anchor not in second_folded:
            raise MonthlyArtifactError(
                f"Rendered Alpha World factsheet is missing the approved page-two anchor: {anchor}"
            )
    display_date = _alpha_world_display_date(inputs.period_end)
    if display_date not in first or display_date not in second:
        raise MonthlyArtifactError("Rendered Alpha World factsheet date is not present on both pages")
    if inputs.reporting_currency not in first:
        raise MonthlyArtifactError("Rendered Alpha World factsheet reporting currency is unavailable")
    page_one_runs = _collect_text_runs(document.pages[0])
    layout = ALPHA_WORLD_FACTSHEET_LAYOUT["page_1"]
    _require_run(
        page_one_runs,
        inputs.reporting_currency,
        x=layout["currency_x"],
        y=layout["currency_y"],
        size=9.15,
        font="Helvetica",
        tolerance=0.2,
    )
    if first.count("BASE CURRENCY") != 1:
        raise MonthlyArtifactError(
            "Rendered Alpha World factsheet contains an overprinted base-currency caption"
        )
    kpi_runs = [
        run
        for run in page_one_runs
        if 516.0 <= run[1] <= layout["metric_value_right"]
        and 540.0 <= run[2] <= 655.0
        and re.fullmatch(r"(?:[+-]?\d+(?:\.\d+)?%?|N/A)", run[0])
    ]
    if len(kpi_runs) != len(layout["metric_baselines"]):
        raise MonthlyArtifactError(
            "Rendered Alpha World KPI table must contain exactly eight governed values"
        )
    for expected_y in layout["metric_baselines"]:
        matches = [run for run in kpi_runs if abs(run[2] - expected_y) <= 0.15]
        if len(matches) != 1:
            raise MonthlyArtifactError(
                "Rendered Alpha World KPI values do not follow the approved row rhythm"
            )
        value, x, _y, size, font = matches[0]
        expected_x = layout["metric_value_right"] - stringWidth(
            value, "Helvetica-Bold", layout["metric_value_font_size"]
        )
        if (
            abs(x - expected_x) > 0.2
            or abs(size - layout["metric_value_font_size"]) > 0.05
            or font != "/Helvetica-Bold"
        ):
            raise MonthlyArtifactError(
                "Rendered Alpha World KPI typography does not match the approved authority"
            )
    risk_free_runs = [
        run
        for run in page_one_runs
        if run[0].startswith("* Risk-Free Rate (1M HIBOR, period average):" if inputs.subject_id == "alpha-world-ms" else "* Risk-Free Rate (period avg.):")
    ]
    if (
        len(risk_free_runs) != 1
        or abs(risk_free_runs[0][2] - layout["risk_free_y"]) > 0.15
        or abs(risk_free_runs[0][3] - 4.1) > 0.05
        or risk_free_runs[0][4] != "/Helvetica-Oblique"
    ):
        raise MonthlyArtifactError(
            "Rendered Alpha World KPI footnote does not match the approved authority"
        )
    _require_run(
        page_one_runs,
        "Capital Path",
        x=(
            layout["capital_chart"][0]
            + 27.0
            + (layout["capital_chart"][2] - 32.0) / 2
            - stringWidth(
                "Capital Path",
                "Helvetica-Bold",
                ALPHA_WORLD_CHART_STYLE["line_title_size"],
            )
            / 2
        ),
        y=(
            layout["capital_chart"][1]
            + 17.0
            + layout["capital_chart"][3]
            - 29.0
            + ALPHA_WORLD_CHART_STYLE["line_title_offset"]
        ),
        size=ALPHA_WORLD_CHART_STYLE["line_title_size"],
        font="Helvetica-Bold",
        tolerance=0.2,
    )
    _require_run(
        page_one_runs,
        "Rebased to 100",
        x=(
            layout["rebased_chart"][0]
            + 27.0
            + (layout["rebased_chart"][2] - 32.0) / 2
            - stringWidth(
                "Rebased to 100",
                "Helvetica-Bold",
                ALPHA_WORLD_CHART_STYLE["line_title_size"],
            )
            / 2
        ),
        y=(
            layout["rebased_chart"][1]
            + 17.0
            + layout["rebased_chart"][3]
            - 29.0
            + ALPHA_WORLD_CHART_STYLE["line_title_offset"]
        ),
        size=ALPHA_WORLD_CHART_STYLE["line_title_size"],
        font="Helvetica-Bold",
        tolerance=0.2,
    )
    _require_run(
        page_one_runs,
        distribution_title,
        x=(
            layout["distribution_chart"][0]
            + 34.0
            + (layout["distribution_chart"][2] - 38.0) / 2
            - stringWidth(
                distribution_title,
                "Helvetica-Bold",
                ALPHA_WORLD_CHART_STYLE["distribution_title_size"],
            )
            / 2
        ),
        y=(
            layout["distribution_chart"][1]
            + 16.0
            + layout["distribution_chart"][3]
            - 28.0
            + ALPHA_WORLD_CHART_STYLE["distribution_title_offset"]
        ),
        size=ALPHA_WORLD_CHART_STYLE["distribution_title_size"],
        font="Helvetica-Bold",
        tolerance=0.2,
    )
    if "Density" not in first:
        raise MonthlyArtifactError(
            "Rendered Alpha World return-distribution chart is missing its density axis"
        )
    start = date.fromisoformat(inputs.normalized_nav[0]["date"])
    end = date.fromisoformat(inputs.normalized_nav[-1]["date"])
    date_ticks = _alpha_world_chart_date_ticks(start, end)
    tick_size=ALPHA_WORLD_MS_CHART_STYLE["tick_font_size"] if inputs.subject_id == "alpha-world-ms" else 4.1
    for chart_key in ("capital_chart", "rebased_chart"):
        chart = layout[chart_key]
        plot_left = chart[0] + 27.0
        plot_width = chart[2] - 32.0
        start_ordinal = start.toordinal()
        span = max(1, end.toordinal() - start_ordinal)
        for tick,label in date_ticks:
            center_x = plot_left + plot_width * (
                tick.toordinal() - start_ordinal
            ) / span
            _require_run(
                page_one_runs,
                label,
                x=center_x - stringWidth(label, "Helvetica", tick_size) / 2,
                y=chart[1] + (10.0 if inputs.subject_id == "alpha-world-ms" else 11.0),
                size=tick_size,
                font="Helvetica",
                tolerance=0.25,
            )
    for legend_anchor in ("Skew-normal fit", "Normal fit", "Base = 100"):
        if legend_anchor not in first:
            raise MonthlyArtifactError(
                "Rendered Alpha World charts are missing the approved legend structure"
            )
    distribution_percent_labels = [
        value
        for value, _x, y, _size, _font in page_one_runs
        if 70.0 <= y <= 115.0 and re.fullmatch(r"-?\d+%", value)
    ]
    if inputs.subject_id != "alpha-world-ms" and any(abs(int(value.rstrip("%"))) > 500 for value in distribution_percent_labels):
        raise MonthlyArtifactError(
            "Rendered Alpha World return-distribution axis is outside a usable range"
        )
    forbidden = ("Top 5 Components", "Component Allocation", "Historical Performance")
    if any(label in first or label in second for label in forbidden):
        raise MonthlyArtifactError(
            "Rendered Alpha World factsheet contains a generic account-layout overlay"
        )
    metadata = document.metadata or {}
    if metadata.get("/LayoutFamily") != "alpha-world-owner-template":
        raise MonthlyArtifactError("Rendered Alpha World factsheet layout family is not locked")
    expected_chart_style = (
        ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION
        if inputs.subject_id == "alpha-world-ms"
        else "alpha-world-family-default"
    )
    if metadata.get("/ChartStyleVersion") != expected_chart_style:
        raise MonthlyArtifactError("Rendered Alpha World chart-style contract is not locked")
    if inputs.subject_id == "alpha-world-ms" and metadata.get(
        "/DistributionHistogramBins"
    ) != str(ALPHA_WORLD_CHART_STYLE["distribution_bins"]):
        raise MonthlyArtifactError(
            "Rendered Alpha World MS histogram does not use the approved bin contract"
        )
    if inputs.subject_id == "alpha-world-ms":
        _validate_alpha_world_ms_allocation(document.pages[1])
        _require_run(
            page_one_runs,
            f"Initial {inputs.reporting_currency} {ALPHA_WORLD_MS_CHART_STYLE['capital_initial']:,.0f}",
            x=84.0, y=455.9, size=ALPHA_WORLD_MS_CHART_STYLE["legend_font_size"], font="Helvetica", tolerance=0.2,
        )
        if metadata.get("/CapitalPathBasis") != "normalized-hypothetical-100000-reporting-currency":
            raise MonthlyArtifactError("Alpha World MS Capital Path illustration basis is not locked")
        # Check actual drawing geometry, not metadata alone. This catches a
        # legacy renderer that writes the current version onto the old charts.
        operations = ContentStream(document.pages[0].get_contents(), document.pages[0].pdf).operations
        rectangles = [tuple(map(float, args)) for args, op in operations if op == b"re"]
        for required_rect in (
            layout["period_box"], layout["distribution_chart_clear"],
            (339.0, 453.5, ALPHA_WORLD_MS_CHART_STYLE["rebased_legend_width"], 7.5),
        ):
            if not any(all(abs(a-b) <= 0.05 for a,b in zip(rect, required_rect)) for rect in rectangles):
                raise MonthlyArtifactError("Alpha World MS chart geometry differs from the screenshot contract")
        if any(value == inputs.reporting_currency and x < 100 and 350 < y < 480
               for value, x, y, _size, _font in page_one_runs):
            raise MonthlyArtifactError("Alpha World MS Capital Path has a redundant vertical currency label")


def _validate_alpha_world_ms_allocation(page) -> None:
    """Check the redraw after its clearing mask, not the underlying template."""
    layout = ALPHA_WORLD_MS_ALLOCATION_LAYOUT
    operations = ContentStream(page.get_contents(), page.pdf).operations
    def matches(args, rect):
        return len(args) == 4 and all(abs(float(a)-b) <= .05 for a,b in zip(args, rect))
    masks = [i for i,(args,op) in enumerate(operations) if op == b're' and matches(args, layout['clear'])]
    if not masks:
        raise MonthlyArtifactError('MS allocation clearing geometry is missing')
    rectangles = [tuple(map(float,args)) for args,op in operations[masks[-1]+1:] if op == b're']
    centers = [x + layout['child_width']/2 for x in layout['child_x']]
    parents = [(centers[i]+centers[i+1])/2 for i in (0,2)]
    expected = [(center-layout['parent_width']/2, layout['parent_y'],layout['parent_width'],layout['parent_height']) for center in parents]
    expected += [(x,layout['child_y'],layout['child_width'],layout['child_height']) for x in layout['child_x']]
    if len(rectangles) != 6 or any(not matches(actual,wanted) for actual,wanted in zip(rectangles,expected)):
        raise MonthlyArtifactError('MS allocation boxes do not have the approved uniform geometry')
    moves = [tuple(map(float,args)) for args,op in operations[masks[-1]+1:] if op == b'm']
    ends = [tuple(map(float,args)) for args,op in operations[masks[-1]+1:] if op == b'l']
    if (moves != [(parent,layout['parent_y']) for parent in parents for _ in range(2)]
            or ends != [(center,layout['child_y']+layout['child_height']) for center in centers]):
        raise MonthlyArtifactError('MS allocation connectors do not meet the centered box edges')
    runs = _collect_text_runs(page)
    for center,label in zip(parents,('70%','30%')):
        _require_run(runs,label,x=center-stringWidth(label,'Helvetica-Bold',12.0)/2,
            y=layout['parent_caption_y'],size=12.0,font='Helvetica-Bold',tolerance=.05)
    headings = ('130% LONG','70% SHORT','150% LONG','150% SHORT')
    captions = (('China & Hong Kong','Portfolio'),('Hang Seng Index',),
        ('US Technology &','BioTechnology'),('S&P500',))
    for center,heading,lines in zip(centers,headings,captions):
        _require_run(runs,heading,x=center-stringWidth(heading,'Helvetica-Bold',10.8)/2,
            y=layout['heading_y'],size=10.8,font='Helvetica-Bold',tolerance=.05)
        for i,text in enumerate(lines):
            y = layout['caption_center_y'] + ((len(lines)-1)/2-i)*layout['caption_line_step']
            _require_run(runs,text,x=center-stringWidth(text,'Helvetica',9.4)/2,
                y=y,size=9.4,font='Helvetica',tolerance=.05)
    for i,(child,label) in enumerate(zip(centers,('91%','-49%','45%','-45%'))):
        center = (parents[i//2]+child)/2 + (-layout['branch_label_offset'] if i%2 == 0 else layout['branch_label_offset'])
        _require_run(runs,label,x=center-stringWidth(label,'Helvetica-Bold',10.8)/2,
            y=layout['branch_label_y'],size=10.8,font='Helvetica-Bold',tolerance=.05)


def render_factsheet(
    snapshot: dict[str, Any],
    *,
    template_path: str | Path,
    output_path: str | Path,
    design_model_path: str | Path | None = None,
) -> dict[str, Any]:
    from .report_profiles import ReportProfileError, snapshot_profile
    try:
        profile = snapshot_profile(snapshot)
    except ReportProfileError as error:
        raise MonthlyArtifactError(str(error)) from error
    if profile is not None and not profile.built_in:
        from .profiled_factsheet import render
        return render(snapshot, template_path=template_path, output_path=output_path,
                      design_model_path=design_model_path)
    if snapshot.get('subject_id') == 'alpha-world-t':
        from .alpha_world_t_factsheet import render
        return render(snapshot, template_path=template_path, output_path=output_path,
                      design_model_path=design_model_path)
    inputs = factsheet_inputs(snapshot)
    is_alpha_world = inputs.subject_id in ALPHA_WORLD_SUBJECT_IDS
    if is_alpha_world and design_model_path is None:
        raise MonthlyArtifactError(
            "Alpha World factsheets require both the uploaded template and completed Alpha World factsheet"
        )
    design_sources = (
        validate_factsheet_design_sources(
            template_path=template_path, design_model_path=design_model_path
        )
        if design_model_path is not None
        else None
    )
    if is_alpha_world:
        _validate_alpha_world_source_layout(
            subject_id=inputs.subject_id,
            template_path=template_path,
            design_model_path=design_model_path,
        )
    # Alpha World MS uses the owner-supplied completed factsheet as its visual
    # authority.  The separate template remains hash-bound supporting evidence,
    # while all variable fields are cleared and redrawn from the frozen monthly
    # snapshot.  This prevents a generic or stale source template from silently
    # changing the approved MS layout in historical or future reports.
    base_template_path = (
        design_model_path
        if inputs.subject_id == "alpha-world-ms" and design_model_path is not None
        else template_path
    )
    template = PdfReader(str(base_template_path))
    if len(template.pages) != 2:
        raise MonthlyArtifactError("Factsheet template must contain exactly two pages")
    overlays = []
    for page_number, page in enumerate(template.pages):
        page_width = float(page.mediabox.width)
        page_height = float(page.mediabox.height)
        buffer = io.BytesIO()
        overlay = canvas.Canvas(buffer, pagesize=(page_width, page_height))
        if page_number == 0 and is_alpha_world:
            _draw_alpha_world_page_one(
                overlay, snapshot, inputs, page_width, page_height
            )
        elif page_number == 1 and is_alpha_world:
            _draw_alpha_world_page_two(overlay, inputs, page_width, page_height)
        elif page_number == 0:
            _draw_factsheet_page_one(overlay, inputs, page_width, page_height)
        else:
            _draw_factsheet_page_two(overlay, inputs, page_width, page_height)
        overlay.save()
        buffer.seek(0)
        overlays.append(PdfReader(buffer).pages[0])

    writer = PdfWriter()
    for page_number,(page, overlay) in enumerate(zip(template.pages, overlays, strict=True)):
        if inputs.subject_id == 'alpha-world-ms':
            page=writer.add_page(page)
            _remove_alpha_world_ms_source_date_text(page,ALPHA_WORLD_FACTSHEET_LAYOUT[f'page_{page_number+1}']['date_box'])
            page.merge_page(overlay)
        else:
            page.merge_page(overlay)
            writer.add_page(page)
    if inputs.subject_id == "alpha-world-ms":
        from .alpha_world_ms_copy import COPY_SHA256, COPY_VERSION, repair_disclaimers
        repair_disclaimers(writer.pages)
        writer.add_metadata({"/MSCopyVersion": COPY_VERSION, "/MSCopySHA256": COPY_SHA256})
        from .alpha_world_ms_subtitle import apply, VERSION as SUBTITLE_VERSION
        apply(writer.pages[0])
        writer.add_metadata({"/MSSubtitleVersion": SUBTITLE_VERSION})
    writer.add_metadata(
        {
            "/Title": f"{inputs.subject_id} Factsheet - {_display_date(inputs.period_end)}",
            "/Subject": "Generated from an immutable frozen reporting snapshot",
            "/Author": "Sprout Asset Management (Asia) Limited",
            "/Producer": "IBKR Portfolio Platform deterministic factsheet renderer",
            "/LayoutFamily": (
                "alpha-world-owner-template" if is_alpha_world else "account-owner-template"
            ),
            "/LayoutAuthoritySHA256": (
                ALPHA_WORLD_MS_FACTSHEET_AUTHORITY_SHA256
                if inputs.subject_id == "alpha-world-ms"
                else ""
            ),
            "/ChartStyleVersion": (
                ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION
                if inputs.subject_id == "alpha-world-ms"
                else "alpha-world-family-default" if is_alpha_world else "account-default"
            ),
            "/DistributionHistogramBins": (
                str(ALPHA_WORLD_CHART_STYLE["distribution_bins"])
                if inputs.subject_id == "alpha-world-ms"
                else ""
            ),
            "/CapitalPathBasis": (
                "normalized-hypothetical-100000-reporting-currency"
                if inputs.subject_id == "alpha-world-ms" else "actual-nav"
            ),
        }
    )
    if inputs.subject_id == "alpha-world-ms":
        writer.add_metadata({"/MSCalculationVersion":inputs.chart_evidence["version"],
            "/MSCalculationEvidence":json.dumps(inputs.chart_evidence,sort_keys=True,allow_nan=False),
            "/MSMetrics":json.dumps(inputs.chart_metrics,sort_keys=True,allow_nan=False)})
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as stream:
        writer.write(stream)
    try:
        if is_alpha_world:
            validate_alpha_world_factsheet_layout(destination, inputs)
            if inputs.subject_id == "alpha-world-ms":
                from .alpha_world_ms_copy import validate_copy
                validate_copy(destination)
                from .alpha_world_ms_pdf_validation import validate
                try:
                    accuracy = validate(destination, snapshot)
                except (ValueError, StopIteration) as error:
                    raise MonthlyArtifactError(str(error)) from error
        else:
            validate_factsheet_layout(destination, inputs)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    result = {
        "period_end": inputs.period_end,
        "data_as_of": inputs.data_as_of,
        "output_path": str(destination),
        "pages": 2,
        "positions": inputs.positions,
        "trailing_twelve_month_return": inputs.trailing_twelve_month_return,
        "layout_version": factsheet_layout_version(inputs.subject_id),
        "layout_family": (
            "alpha-world-owner-template" if is_alpha_world else "account-owner-template"
        ),
    }
    if design_sources is not None:
        result.update(design_sources)
        result["render_contract_sha256"] = factsheet_render_contract_sha256(
            subject_id=inputs.subject_id,
            supporting_template_sha256=design_sources[
                "supporting_template_sha256"
            ],
            completed_example_sha256=design_sources["completed_example_sha256"],
        )
        result["design_source"] = "subject_completed_factsheet"
    if inputs.subject_id == "alpha-world-ms":
        result["chart_calculation"]={"metrics":inputs.chart_metrics,**inputs.chart_evidence}
        result["quality_status"]="coverage_warning" if inputs.chart_evidence["excluded_intervals"] else "pass"
        result["pdf_accuracy_validation"] = accuracy
    return result


def load_snapshot(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
