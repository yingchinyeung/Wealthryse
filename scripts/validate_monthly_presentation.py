from __future__ import annotations

import argparse
import re
import unicodedata
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from ibkr_etl.presentation_design import validate_platform_native_evidence


PRESENTATION_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
CHART_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"

def _required_page_titles(*, ms_hkd: bool = False) -> tuple[str, ...]:
    return (
        "Monthly Security Contribution",
        "Component Drivers of Monthly Return" if ms_hkd else "Sector Drivers of Relative Performance",
        "Dividends and Financing",
    )


def _identity(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _validate_rebased_performance_axis(
    package: zipfile.ZipFile, names: set[str]
) -> dict[str, float]:
    matching_axes: list[tuple[float, float, float]] = []
    for name in sorted(names):
        if not re.fullmatch(r"ppt/(?:slides/)?charts/chart\d+\.xml", name):
            continue
        root = ElementTree.fromstring(package.read(name))
        title = " ".join(
            element.text or "" for element in root.iter(f"{{{DRAWING_NS}}}t")
        )
        if "Rebased performance comparison" not in title:
            continue
        axes = root.findall(f".//{{{CHART_NS}}}valAx")
        if len(axes) != 1:
            raise RuntimeError("Rebased performance chart must contain one value axis")
        axis = axes[0]
        scaling = axis.find(f"{{{CHART_NS}}}scaling")
        minimum = None if scaling is None else scaling.find(f"{{{CHART_NS}}}min")
        maximum = None if scaling is None else scaling.find(f"{{{CHART_NS}}}max")
        major_unit = axis.find(f"{{{CHART_NS}}}majorUnit")
        if minimum is None or maximum is None or major_unit is None:
            raise RuntimeError("Rebased performance chart axis bounds are not explicit")
        values = (
            float(minimum.get("val", "nan")),
            float(maximum.get("val", "nan")),
            float(major_unit.get("val", "nan")),
        )
        matching_axes.append(values)
    if len(matching_axes) != 1:
        raise RuntimeError("Expected exactly one rebased performance chart")
    minimum, maximum, major_unit = matching_axes[0]
    if major_unit != 10 or minimum % 10 != 0 or maximum % 10 != 0 or maximum <= minimum:
        raise RuntimeError(
            "Rebased performance chart must use outward-rounded 10-point increments"
        )
    return {"minimum": minimum, "maximum": maximum, "major_unit": major_unit}


def _chart_categories(root: ElementTree.Element) -> list[str]:
    categories = root.find(f".//{{{CHART_NS}}}cat")
    if categories is None:
        return []
    return [
        (value.text or "").strip()
        for value in categories.findall(f".//{{{CHART_NS}}}v")
        if (value.text or "").strip()
    ]


def _validate_security_axis_labels(
    package: zipfile.ZipFile,
    names: set[str],
    expected: dict[str, list[str]],
) -> dict[str, list[str]]:
    actual: dict[str, list[str]] = {}
    for name in sorted(names):
        if not re.fullmatch(r"ppt/(?:slides/)?charts/chart\d+\.xml", name):
            continue
        root = ElementTree.fromstring(package.read(name))
        title = " ".join(
            element.text or "" for element in root.iter(f"{{{DRAWING_NS}}}t")
        )
        for title_prefix in expected:
            if title_prefix in title:
                if title_prefix in actual:
                    raise RuntimeError(
                        f"Expected exactly one chart beginning {title_prefix!r}"
                    )
                actual[title_prefix] = _chart_categories(root)
    for title_prefix, expected_labels in expected.items():
        actual_labels = actual.get(title_prefix)
        if actual_labels is None:
            raise RuntimeError(f"Missing security chart beginning {title_prefix!r}")
        if actual_labels != expected_labels:
            raise RuntimeError(
                f"{title_prefix} must use security names from the frozen snapshot; "
                f"expected {expected_labels!r}, found {actual_labels!r}"
            )
    return actual


def _table_rows(root: ElementTree.Element) -> dict[str, str]:
    rows: dict[str, str] = {}
    for row in root.findall(f".//{{{DRAWING_NS}}}tbl/{{{DRAWING_NS}}}tr"):
        values = [
            "".join(
                element.text or ""
                for element in cell.iter(f"{{{DRAWING_NS}}}t")
            ).strip()
            for cell in row.findall(f"./{{{DRAWING_NS}}}tc")
        ]
        if len(values) >= 2 and values[0]:
            rows[values[0]] = values[1]
    return rows


def _validate_summary_table_rows(
    package: zipfile.ZipFile,
    names: set[str],
    expected: dict[int, dict[str, str]],
) -> dict[int, dict[str, str]]:
    actual: dict[int, dict[str, str]] = {}
    for slide_number, expected_rows in expected.items():
        slide_name = f"ppt/slides/slide{slide_number}.xml"
        if slide_name not in names:
            raise RuntimeError(f"Missing slide {slide_number} for summary-table validation")
        rows = _table_rows(ElementTree.fromstring(package.read(slide_name)))
        actual[slide_number] = {}
        for label, expected_value in expected_rows.items():
            actual_value = rows.get(label)
            if actual_value is None:
                raise RuntimeError(
                    f"Slide {slide_number} is missing summary-table row {label!r}"
                )
            if actual_value != expected_value:
                raise RuntimeError(
                    f"Slide {slide_number} row {label!r} must use the frozen "
                    f"snapshot value; expected {expected_value!r}, found "
                    f"{actual_value!r}"
                )
            actual[slide_number][label] = actual_value
    return actual


def _validate_slide_text(
    package: zipfile.ZipFile,
    names: set[str],
    expected: dict[int, list[str]],
) -> dict[int, list[str]]:
    actual: dict[int, list[str]] = {}
    for slide_number, expected_fragments in expected.items():
        slide_name = f"ppt/slides/slide{slide_number}.xml"
        if slide_name not in names:
            raise RuntimeError(f"Missing slide {slide_number} for text validation")
        root = ElementTree.fromstring(package.read(slide_name))
        slide_text = " ".join(
            element.text or "" for element in root.iter(f"{{{DRAWING_NS}}}t")
        )
        actual[slide_number] = []
        for expected_fragment in expected_fragments:
            if expected_fragment not in slide_text:
                raise RuntimeError(
                    f"Slide {slide_number} is missing required frozen-snapshot "
                    f"text {expected_fragment!r}"
                )
            actual[slide_number].append(expected_fragment)
    return actual


def validate(
    path: Path,
    *,
    expected_period: str,
    expected_strategy: str | None = None,
    expected_security_axis_labels: dict[str, list[str]] | None = None,
    expected_summary_table_rows: dict[int, dict[str, str]] | None = None,
    expected_slide_text: dict[int, list[str]] | None = None,
    ms_hkd: bool = False,
    composite_design: bool = False,
) -> dict[str, object]:
    if ms_hkd and _identity(expected_strategy or "") != _identity("iStrategy: Alpha World MS"):
        raise RuntimeError("MS HKD content validation requires the Alpha World MS identity")
    if not path.is_file() or path.stat().st_size < 100_000:
        raise RuntimeError("Presentation output is missing or unexpectedly small")
    with zipfile.ZipFile(path) as package:
        if package.testzip() is not None:
            raise RuntimeError("PowerPoint package integrity check failed")
        names = set(package.namelist())
        slides = sorted(
            name
            for name in names
            if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
        )
        if len(slides) != 20:
            raise RuntimeError(f"Expected 20 slides, found {len(slides)}")
        root = ElementTree.fromstring(package.read("ppt/presentation.xml"))
        size = root.find(f"{{{PRESENTATION_NS}}}sldSz")
        if size is None or (size.get("cx"), size.get("cy")) != (
            "9144000",
            "5143500",
        ):
            raise RuntimeError("Presentation dimensions drifted from the approved template")
        paragraphs = [
            "".join(node.text or "" for node in paragraph.iter(f"{{{DRAWING_NS}}}t"))
            for slide in slides
            for paragraph in ElementTree.fromstring(package.read(slide)).iter(
                f"{{{DRAWING_NS}}}p"
            )
        ]
        text = " ".join(
            element.text or ""
            for slide in slides
            for element in ElementTree.fromstring(package.read(slide)).iter(
                f"{{{DRAWING_NS}}}t"
            )
        )
        forbidden = (
            "internal staging use only",
            "provisional yahoo finance classification",
            "next release gate",
            "benchmark and sector data are provisional",
        )
        if any(value in text.lower() for value in forbidden):
            raise RuntimeError("Presentation contains a removed development warning")
        if expected_strategy:
            expected_identity = _identity(expected_strategy)
            if expected_identity not in _identity(text):
                raise RuntimeError(
                    f"Presentation is missing required strategy name {expected_strategy!r}"
                )
            strategy_markers = [
                paragraph
                for paragraph in paragraphs
                if re.match(r"^\s*istrategy\s*:", paragraph, re.I)
            ]
            full_strategy_markers = [
                marker for marker in strategy_markers if _identity(marker) != "istrategy"
            ]
            if not full_strategy_markers or any(
                expected_identity not in _identity(marker)
                for marker in full_strategy_markers
            ):
                raise RuntimeError(
                    "Presentation contains a strategy name from another account"
                )
        # A composite in the Alpha World MS or T design keeps MS's absolute-return heading.
        for required_title in _required_page_titles(ms_hkd=ms_hkd or composite_design):
            if required_title not in text:
                raise RuntimeError(
                    f"Presentation is missing approved v23 page {required_title!r}"
                )
        if any(name.startswith("ppt/comments/") for name in names):
            raise RuntimeError("Presentation contains an unresolved comment part")
        year, month, _day = expected_period.split("-")
        if year not in text:
            raise RuntimeError("Presentation does not contain the expected report year")
        performance_axis = _validate_rebased_performance_axis(package, names)
        security_axis_labels = (
            _validate_security_axis_labels(
                package, names, expected_security_axis_labels
            )
            if expected_security_axis_labels
            else {}
        )
        summary_table_rows = (
            _validate_summary_table_rows(
                package, names, expected_summary_table_rows
            )
            if expected_summary_table_rows
            else {}
        )
        slide_text = (
            _validate_slide_text(package, names, expected_slide_text)
            if expected_slide_text
            else {}
        )
    largest_detractor = str(
        (expected_summary_table_rows or {}).get(9, {}).get("Largest detractor", "N/A")
    )
    platform_native_evidence = validate_platform_native_evidence(
        path,
        require_negative_colour=largest_detractor.strip().upper() != "N/A",
    )
    return {
        "slides": 20,
        "period_end": expected_period,
        "bytes": path.stat().st_size,
        "performance_axis": performance_axis,
        "security_axis_labels": security_axis_labels,
        "summary_table_rows": summary_table_rows,
        "slide_text": slide_text,
        "platform_native_evidence": platform_native_evidence,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("presentation", type=Path)
    parser.add_argument("--expected-period", required=True)
    parser.add_argument("--expected-strategy")
    args = parser.parse_args()
    print(
        validate(
            args.presentation,
            expected_period=args.expected_period,
            expected_strategy=args.expected_strategy,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
