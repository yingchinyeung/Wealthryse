from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


PRESENTATION_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
CHART_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"

PLATFORM_PRESENTATION_REFERENCE_SUBJECT = "U16088571"
PLATFORM_PRESENTATION_LAYOUT_VERSION = "approved-august-2026-20-slide-v24"
PLATFORM_PRESENTATION_SHA256 = (
    "4393eda47945c9df17167e8cbe5278afe90f20b16f52bd473ff3e7549a759191"
)
PLATFORM_PRESENTATION_SLIDE_COUNT = 20
PLATFORM_PRESENTATION_TABLE_SLIDES = (3, 5, 6, 9, 10, 11, 12, 13, 14, 15, 16)
PLATFORM_PRESENTATION_CHART_SLIDES = (6, 7, 8, 9, 10, 11)
PLATFORM_PRESENTATION_REQUIRED_COLOURS = (
    "111827",  # approved navy table headers and risk charts
    "00884A",  # Sprout green
    "54BFAF",  # approved mint comparison series
    "D9E0E8",  # approved table and chart grid
)
PLATFORM_PRESENTATION_NEGATIVE_COLOUR = "C94F52"
PLATFORM_PRESENTATION_CONTENT_CONTRACT_VERSION = (
    "platform-standard-v24-reporting-currency-full-nav-v9"
)
PLATFORM_PRESENTATION_CONTENT_REQUIREMENTS = (
    "Slides 6 and 9 chart categories use security names from the frozen snapshot.",
    "Holdings and sector weights use dated reporting-currency values; benchmark captions follow the frozen comparison.",
    "Physical monthly attribution includes every frozen NAV interval and discloses residual-only position gaps.",
    "Slide 9 shows the largest contributor and detractor by security name.",
    "Slide 6 commentary names the largest holding and highest-weight sector from the snapshot.",
    "Every historical and future deck fails closed unless output-level validation passes.",
)
ALPHA_WORLD_MS_PRESENTATION_CONTENT_CONTRACT_VERSION = (
    "alpha-world-ms-v24-hkd-hibor-crisis-evidence-v3"
)
ALPHA_WORLD_MS_PRESENTATION_CONTENT_REQUIREMENTS = (
    *PLATFORM_PRESENTATION_CONTENT_REQUIREMENTS,
    "MS uses qualified HKD NAV and inception-to-date average published 1M HIBOR.",
    "All six exported charts and seven risk metrics match the frozen MS snapshot.",
    "Six embedded chart-data workbooks match every category, value and series header.",
    "Contribution magnitudes retain signed labels and the absolute monthly-return heading.",
    "One observed month has a visible marker without synthetic observations.",
    "Benchmark results remain unavailable; HKD crisis results require independent qualification; NAV interest is not charged twice.",
)


def presentation_content_contract_version(subject_id: str) -> str:
    """Select the subject's output contract, not a new physical template."""
    if subject_id == "alpha-world-ms":
        return ALPHA_WORLD_MS_PRESENTATION_CONTENT_CONTRACT_VERSION
    if subject_id == 'alpha-world-t':
        return 'alpha-world-t-v24-hkd-hibor-internal-crisis-v1'
    return PLATFORM_PRESENTATION_CONTENT_CONTRACT_VERSION


def presentation_content_requirements(subject_id: str) -> tuple[str, ...]:
    if subject_id == "alpha-world-ms":
        return ALPHA_WORLD_MS_PRESENTATION_CONTENT_REQUIREMENTS
    if subject_id == 'alpha-world-t':
        return (
            'T uses full Greater China including Taiwan, fixed units, HKD and inception-to-date average 1M HIBOR.',
            'Six exported charts and thirteen risk numbers match recomputed frozen inputs.',
            'Six editable chart workbooks match categories, values and series headers.',
            'Monthly fixed-unit component effects sum to the NAV return.',
            'Crisis replay is source-bound, provisional and internal/shareholder only; no provider certification is implied.',
            'Short-history estimates and unqualified benchmark comparisons are explicitly unavailable.',
        )
    return PLATFORM_PRESENTATION_CONTENT_REQUIREMENTS


def platform_content_expectations(
    payload: dict[str, Any],
) -> dict[str, dict[Any, Any]]:
    """Build account-independent presentation assertions from one frozen snapshot."""

    is_ms_hkd=payload.get('subject_id')=='alpha-world-ms' and payload.get('reporting_currency')=='HKD'
    if is_ms_hkd:
        from .alpha_world_ms_presentation import build_model
        build_model(payload)  # Do not accept a relabelled or inconsistent snapshot.
    else:
        currency = payload.get('reporting_currency')
        if (currency != 'USD' or payload.get('holdings', {}).get('currency') != currency
                or payload.get('sector_allocation', {}).get('currency') != currency
                or any(row.get('currency') != currency for row in payload.get('holdings', {}).get('holdings', []))):
            raise ValueError('Presentation holdings and allocation must use the reporting currency')
    holdings = sorted(
        payload["holdings"]["holdings"],
        key=lambda row: abs(float(row["market_value_text"])),
        reverse=True,
    )
    if not holdings:
        raise RuntimeError("Presentation content validation requires at least one holding")
    monthly_security = list(payload["monthly_reporting"]["security_contributions"])
    detractors = sorted(
        (row for row in monthly_security if float(row["contribution"]) < 0),
        key=lambda row: float(row["contribution"]),
    )[:5]
    contributors = sorted(
        (row for row in monthly_security if float(row["contribution"]) > 0),
        key=lambda row: float(row["contribution"]),
        reverse=True,
    )[:5]
    contribution_rows = detractors + list(reversed(contributors))
    if not contribution_rows:
        contribution_rows = [
            {"symbol": "No effect", "description": "", "contribution": 0}
        ]

    def security_name(row: dict[str, Any] | None) -> str:
        if row is None:
            return "N/A"
        return " ".join(str(row.get("description") or "").split()) or str(
            row.get("symbol") or "Security"
        ).strip()

    gross_market_value = float(payload["sector_allocation"]["gross_market_value"])
    if gross_market_value == 0:
        raise RuntimeError("Presentation content validation requires non-zero gross holdings")
    net_asset_value = float(payload["portfolio_summary"]["latest_nav"]["total_text"])
    allocation = list(payload["sector_allocation"]["allocation"])
    if not allocation:
        raise RuntimeError("Presentation content validation requires sector allocation")
    largest_sector = max(allocation, key=lambda row: float(row["weight"]))
    is_composite = (
        payload["sector_allocation"].get("classification_basis")
        == "composite sleeve type"
    )
    holding_noun = "component" if is_composite else "holding"
    allocation_noun = "component type" if is_composite else "sector"
    largest_holding_weight = abs(float(holdings[0]["market_value_text"])) / gross_market_value
    positive_contributions = [
        row for row in monthly_security if float(row["contribution"]) > 0
    ]
    negative_contributions = [
        row for row in monthly_security if float(row["contribution"]) < 0
    ]
    largest_contributor = max(
        positive_contributions,
        key=lambda row: float(row["contribution"]),
        default=None,
    )
    largest_detractor = min(
        negative_contributions,
        key=lambda row: float(row["contribution"]),
        default=None,
    )

    def percent(value: float) -> str:
        return f"{value * 100:.2f}%"

    def points(row: dict[str, Any] | None) -> str:
        return "N/A" if row is None else f"{float(row['contribution']) * 100:.2f} pp"

    return {
        "security_axis_labels": {
            f"Top 10 {holding_noun}s": [security_name(row) for row in holdings[:10]],
            "Top five contributors": [
                security_name(row) for row in contribution_rows
            ],
        },
        "summary_table_rows": {
            6: {
                ("Largest component" if is_composite else "Largest holding"): (
                    f"{security_name(holdings[0])} · {percent(largest_holding_weight)}"
                ),
                "Net asset value": (f"HKD {net_asset_value:,.2f}" if is_ms_hkd else f"${net_asset_value:,.2f}"),
            },
            9: {
                "Largest contributor": (
                    "N/A"
                    if largest_contributor is None
                    else f"{security_name(largest_contributor)} · {points(largest_contributor)}"
                ),
                "Largest detractor": (
                    "N/A"
                    if largest_detractor is None
                    else f"{security_name(largest_detractor)} · {points(largest_detractor)}"
                ),
            },
        },
        "slide_text": {
            **({3: ['BENCHMARK EXCESS'],
                7: ['Growth of 100 — portfolio versus ' + str(payload.get('benchmark', {}).get('name') or payload.get('benchmark', {}).get('benchmark_id') or 'Primary benchmark')]}
               if not is_ms_hkd else {}),
            6: [
                (
                    f"{security_name(holdings[0])} is the largest {holding_noun}. "
                    f"{largest_sector['sector']} has the highest {allocation_noun} weight."
                )
            ],
        },
    }


def _numbered_name(name: str) -> int:
    match = re.search(r"(\d+)", name)
    if match is None:
        raise RuntimeError(f"Presentation part has no numeric index: {name}")
    return int(match.group(1))


def _remove_chart_caches(root: ElementTree.Element) -> None:
    cache_tags = {
        f"{{{CHART_NS}}}numCache",
        f"{{{CHART_NS}}}strCache",
        f"{{{CHART_NS}}}multiLvlStrCache",
    }
    for parent in root.iter():
        for child in list(parent):
            if child.tag in cache_tags:
                parent.remove(child)
    for element in root.iter():
        if element.tag == f"{{{DRAWING_NS}}}t":
            element.text = ""
        if element.tag in {
            f"{{{CHART_NS}}}min",
            f"{{{CHART_NS}}}max",
            f"{{{CHART_NS}}}majorUnit",
        }:
            element.attrib.pop("val", None)


def platform_layout_structure_sha256(path: Path) -> str:
    """Hash native layout and style while excluding account data and labels."""

    with zipfile.ZipFile(path) as package:
        if package.testzip() is not None:
            raise RuntimeError("PowerPoint package integrity check failed")
        names = set(package.namelist())
        parts = sorted(
            (
                name
                for name in names
                if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
                or re.fullmatch(r"ppt/(?:slides/)?charts/chart\d+\.xml", name)
            ),
            key=lambda name: ("/charts/" in name, _numbered_name(name)),
        )
        if len([name for name in parts if "/slides/" in name]) != PLATFORM_PRESENTATION_SLIDE_COUNT:
            raise RuntimeError("Presentation does not use the approved 20-slide structure")
        digest = hashlib.sha256()
        for name in parts:
            root = ElementTree.fromstring(package.read(name))
            if "/slides/" in name:
                for element in root.iter(f"{{{DRAWING_NS}}}t"):
                    element.text = ""
            else:
                _remove_chart_caches(root)
            digest.update(name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(ElementTree.tostring(root, encoding="utf-8"))
            digest.update(b"\0")
        return digest.hexdigest()


def validate_platform_native_evidence(
    path: Path,
    *,
    require_negative_colour: bool = False,
) -> dict[str, object]:
    """Require the platform's editable chart, table and contextual palette.

    The red accent is semantic rather than structural: it must be present when
    the frozen snapshot contains a negative contributor, but a month with no
    negative contribution must not fail merely because there is nothing that
    should be rendered in red.
    """

    with zipfile.ZipFile(path) as package:
        names = set(package.namelist())
        missing_tables: list[int] = []
        missing_charts: list[int] = []
        for slide_number in PLATFORM_PRESENTATION_TABLE_SLIDES:
            root = ElementTree.fromstring(
                package.read(f"ppt/slides/slide{slide_number}.xml")
            )
            if root.find(f".//{{{DRAWING_NS}}}tbl") is None:
                missing_tables.append(slide_number)
        for slide_number in PLATFORM_PRESENTATION_CHART_SLIDES:
            root = ElementTree.fromstring(
                package.read(f"ppt/slides/slide{slide_number}.xml")
            )
            if root.find(f".//{{{CHART_NS}}}chart") is None:
                missing_charts.append(slide_number)
        if missing_tables:
            raise RuntimeError(
                "Presentation is missing approved native tables on slide(s): "
                + ", ".join(map(str, missing_tables))
            )
        if missing_charts:
            raise RuntimeError(
                "Presentation is missing approved native charts on slide(s): "
                + ", ".join(map(str, missing_charts))
            )
        required_colours = list(PLATFORM_PRESENTATION_REQUIRED_COLOURS)
        if require_negative_colour:
            required_colours.append(PLATFORM_PRESENTATION_NEGATIVE_COLOUR)
        colour_counts: dict[str, int] = {colour: 0 for colour in required_colours}
        colour_pattern = re.compile(rb'(?i)(?:val|lastClr)="([0-9a-f]{6})"')
        for name in names:
            if not name.startswith("ppt/") or not name.endswith(".xml"):
                continue
            for match in colour_pattern.findall(package.read(name)):
                value = match.decode("ascii").upper()
                if value in colour_counts:
                    colour_counts[value] += 1
        missing_colours = [
            colour for colour, count in colour_counts.items() if count == 0
        ]
        if missing_colours:
            raise RuntimeError(
                "Presentation is missing approved platform colour(s): "
                + ", ".join(missing_colours)
            )
        return {
            "native_table_slides": list(PLATFORM_PRESENTATION_TABLE_SLIDES),
            "native_chart_slides": list(PLATFORM_PRESENTATION_CHART_SLIDES),
            "chart_part_count": len(
                [
                    name
                    for name in names
                    if re.fullmatch(r"ppt/(?:slides/)?charts/chart\d+\.xml", name)
                ]
            ),
            "required_colour_counts": colour_counts,
            "negative_colour_required": require_negative_colour,
        }
