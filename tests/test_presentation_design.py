from __future__ import annotations

import hashlib
import unittest
from pathlib import Path
from unittest.mock import patch

from ibkr_etl.presentation_design import (
    PLATFORM_PRESENTATION_CHART_SLIDES,
    PLATFORM_PRESENTATION_CONTENT_CONTRACT_VERSION,
    PLATFORM_PRESENTATION_CONTENT_REQUIREMENTS,
    PLATFORM_PRESENTATION_SHA256,
    PLATFORM_PRESENTATION_TABLE_SLIDES,
    platform_content_expectations,
    validate_platform_native_evidence,
)


class _FakePresentationPackage:
    def __init__(
        self,
        *,
        missing_chart: int | None = None,
        omit_grid: bool = False,
        omit_negative: bool = False,
    ):
        self.missing_chart = missing_chart
        self.omit_grid = omit_grid
        self.omit_negative = omit_negative
        self._names = [f"ppt/slides/slide{number}.xml" for number in range(1, 21)]
        self._names.extend(
            f"ppt/slides/charts/chart{number}.xml" for number in range(1, 7)
        )

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def testzip(self):
        return None

    def namelist(self):
        return list(self._names)

    def read(self, name: str) -> bytes:
        if "/charts/" in name:
            return b'<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" />'
        slide_number = int(name.rsplit("slide", 1)[1].split(".", 1)[0])
        table = "<a:tbl />" if slide_number in PLATFORM_PRESENTATION_TABLE_SLIDES else ""
        chart = (
            "<c:chart />"
            if slide_number in PLATFORM_PRESENTATION_CHART_SLIDES
            and slide_number != self.missing_chart
            else ""
        )
        colours = ["111827", "00884A", "54BFAF"]
        if not self.omit_negative:
            colours.append("C94F52")
        if not self.omit_grid:
            colours.append("D9E0E8")
        colour_xml = "".join(f'<a:srgbClr val="{value}" />' for value in colours)
        return (
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart">'
            f"{table}{chart}{colour_xml}</p:sld>"
        ).encode("utf-8")


class PresentationDesignTests(unittest.TestCase):
    def test_future_account_uses_platform_content_contract(self) -> None:
        payload = {
            "subject": {"subject_id": "U99999999"},
            "reporting_currency": "USD",
            "holdings": {
                "currency": "USD",
                "holdings": [
                    {
                        "symbol": "NEW1",
                        "currency": "USD",
                        "description": "Future Account Security",
                        "market_value_text": "600.00",
                    },
                    {
                        "symbol": "NEW2",
                        "currency": "USD",
                        "description": "Second Future Security",
                        "market_value_text": "400.00",
                    },
                ]
            },
            "monthly_reporting": {
                "security_contributions": [
                    {
                        "symbol": "NEW1",
                        "description": "Future Account Security",
                        "contribution": 0.0123,
                    },
                    {
                        "symbol": "NEW2",
                        "description": "Second Future Security",
                        "contribution": -0.0045,
                    },
                ]
            },
            "sector_allocation": {
                "currency": "USD",
                "gross_market_value": 1000,
                "allocation": [
                    {"sector": "Technology", "weight": 0.6},
                    {"sector": "Health Care", "weight": 0.4},
                ],
            },
            "portfolio_summary": {"latest_nav": {"total_text": "1234.56"}},
        }

        expectations = platform_content_expectations(payload)
        self.assertEqual(expectations['slide_text'][3], ['BENCHMARK EXCESS'])
        self.assertEqual(expectations['slide_text'][7], ['Growth of 100 — portfolio versus Primary benchmark'])
        for location in ('holding', 'holdings', 'allocation'):
            import copy
            invalid = copy.deepcopy(payload)
            if location == 'holding':
                invalid['holdings']['holdings'][0]['currency'] = 'HKD'
            elif location == 'holdings':
                invalid['holdings']['currency'] = 'HKD'
            else:
                invalid['sector_allocation']['currency'] = 'HKD'
            with self.subTest(currency_location=location), self.assertRaisesRegex(ValueError, 'reporting currency'):
                platform_content_expectations(invalid)

        self.assertEqual(
            expectations["security_axis_labels"]["Top 10 holdings"],
            ["Future Account Security", "Second Future Security"],
        )
        self.assertEqual(
            expectations["summary_table_rows"][6],
            {
                "Largest holding": "Future Account Security · 60.00%",
                "Net asset value": "$1,234.56",
            },
        )
        self.assertEqual(
            expectations["summary_table_rows"][9]["Largest contributor"],
            "Future Account Security · 1.23 pp",
        )
        self.assertEqual(
            expectations["slide_text"][6],
            [
                "Future Account Security is the largest holding. "
                "Technology has the highest sector weight."
            ],
        )
        self.assertTrue(PLATFORM_PRESENTATION_CONTENT_CONTRACT_VERSION.startswith("platform-standard"))
        self.assertEqual(len(PLATFORM_PRESENTATION_CONTENT_REQUIREMENTS), 6)
        self.assertIn("every frozen NAV interval", PLATFORM_PRESENTATION_CONTENT_REQUIREMENTS[2])

        payload["sector_allocation"]["classification_basis"] = "composite sleeve type"
        payload["sector_allocation"]["allocation"][0]["sector"] = "Portfolio"
        composite = platform_content_expectations(payload)
        self.assertIn("Top 10 components", composite["security_axis_labels"])
        self.assertIn("Largest component", composite["summary_table_rows"][6])
        self.assertIn("highest component type weight", composite["slide_text"][6][0])

        payload["monthly_reporting"]["security_contributions"] = [
            {
                "symbol": "NEW1",
                "description": "Future Account Security",
                "contribution": 0.0123,
            },
            {
                "symbol": "NEW2",
                "description": "Second Future Security",
                "contribution": 0.0045,
            },
        ]
        positive_only = platform_content_expectations(payload)
        self.assertEqual(
            positive_only["summary_table_rows"][9]["Largest detractor"], "N/A"
        )

        payload["monthly_reporting"]["security_contributions"] = [
            {
                "symbol": "NEW1",
                "description": "Future Account Security",
                "contribution": -0.0123,
            },
            {
                "symbol": "NEW2",
                "description": "Second Future Security",
                "contribution": -0.0045,
            },
        ]
        negative_only = platform_content_expectations(payload)
        self.assertEqual(
            negative_only["summary_table_rows"][9]["Largest contributor"], "N/A"
        )

    def test_approved_u16088571_model_passes_native_evidence_gate(self) -> None:
        path = Path(
            "output/pptx/U16088571_Monthly_Presentation_2026-08-31_Enhanced_v24.pptx"
        )
        if not path.is_file():
            path = Path('templates/U16088571/approved-august-2026-20-slide-v24.pptx')
        self.assertTrue(path.is_file())
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), PLATFORM_PRESENTATION_SHA256)
        evidence = validate_platform_native_evidence(path)
        self.assertEqual(evidence["native_table_slides"], list(PLATFORM_PRESENTATION_TABLE_SLIDES))
        self.assertEqual(evidence["native_chart_slides"], list(PLATFORM_PRESENTATION_CHART_SLIDES))
        self.assertEqual(evidence["chart_part_count"], 6)
        self.assertEqual(len(PLATFORM_PRESENTATION_SHA256), 64)

    def test_missing_native_chart_fails_closed(self) -> None:
        package = _FakePresentationPackage(missing_chart=7)
        with patch("ibkr_etl.presentation_design.zipfile.ZipFile", return_value=package):
            with self.assertRaisesRegex(RuntimeError, "native charts on slide.*7"):
                validate_platform_native_evidence(Path("ignored.pptx"))

    def test_platform_palette_drift_fails_closed(self) -> None:
        package = _FakePresentationPackage(omit_grid=True)
        with patch("ibkr_etl.presentation_design.zipfile.ZipFile", return_value=package):
            with self.assertRaisesRegex(RuntimeError, "D9E0E8"):
                validate_platform_native_evidence(Path("ignored.pptx"))

    def test_negative_colour_is_optional_when_snapshot_has_no_detractor(self) -> None:
        package = _FakePresentationPackage(omit_negative=True)
        with patch("ibkr_etl.presentation_design.zipfile.ZipFile", return_value=package):
            evidence = validate_platform_native_evidence(Path("ignored.pptx"))
        self.assertFalse(evidence["negative_colour_required"])
        self.assertNotIn("C94F52", evidence["required_colour_counts"])

    def test_negative_colour_is_required_when_snapshot_has_a_detractor(self) -> None:
        package = _FakePresentationPackage(omit_negative=True)
        with patch("ibkr_etl.presentation_design.zipfile.ZipFile", return_value=package):
            with self.assertRaisesRegex(RuntimeError, "C94F52"):
                validate_platform_native_evidence(
                    Path("ignored.pptx"), require_negative_colour=True
                )


if __name__ == "__main__":
    unittest.main()
