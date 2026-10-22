from __future__ import annotations

import copy
import hashlib
import io
import math
import tempfile
import unittest
from calendar import monthrange
from datetime import date
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream
from reportlab.pdfgen import canvas

from ibkr_etl.monthly_artifacts import (
    ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION,
    FACTSHEET_LAYOUT_VERSION,
    FACTSHEET_REFERENCE_SERIF_FONT,
    MonthlyArtifactError,
    _factsheet_sector_rows,
    _normalized_nav_axis,
    SUBJECT_FACTSHEET_LAYOUT_VERSION,
    factsheet_render_contract_sha256,
    factsheet_inputs,
    render_factsheet,
    validate_factsheet_return_typography,
)


def _snapshot() -> dict:
    return {
        "period_end": "2026-01-31",
        "data_as_of": "2026-01-30",
        "subject_id": "U16088571",
        "reporting_currency": "USD",
        "portfolio_summary": {
            "latest_nav": {"total_text": "110000"},
            "positions": 2,
        },
        "performance": {
            "series": [
                {"date": "2025-01-20", "nav": 100000},
                {"date": "2025-01-31", "nav": 99000},
                {"date": "2026-01-30", "nav": 110000},
            ]
        },
        "holdings": {
            "holdings": [
                {
                    "conid": "1",
                    "description": "ONE INC",
                    "symbol": "ONE",
                    "currency": "USD",
                    "market_value_text": "60000",
                },
                {
                    "conid": "2",
                    "description": "TWO INC",
                    "symbol": "TWO",
                    "currency": "USD",
                    "market_value_text": "40000",
                },
            ]
        },
        "sector_allocation": {
            "gross_market_value": 100000,
            "classifications": [
                {
                    "conid": "1",
                    "symbol": "ONE",
                    "region": "North America",
                    "sector": "Technology",
                    "sub_sector": "Software",
                    "market_value": 60000,
                    "weight": 0.6,
                },
                {
                    "conid": "2",
                    "symbol": "TWO",
                    "region": "North America",
                    "sector": "Healthcare",
                    "sub_sector": "Biotechnology",
                    "market_value": 40000,
                    "weight": 0.4,
                },
            ],
            "allocation_detail": [
                {
                    "region": "North America",
                    "sector": "Technology",
                    "sub_sector": "Software",
                    "holdings": 1,
                    "market_value": 60000,
                    "weight": 0.6,
                },
                {
                    "region": "North America",
                    "sector": "Healthcare",
                    "sub_sector": "Biotechnology",
                    "holdings": 1,
                    "market_value": 40000,
                    "weight": 0.4,
                },
            ],
            "allocation": [
                {"sector": "Technology", "holdings": 1, "market_value": 60000, "weight": 0.6},
                {"sector": "Healthcare", "holdings": 1, "market_value": 40000, "weight": 0.4},
            ],
        },
        "month_end_performance": [
            {
                "date": "2025-01-31",
                "portfolio": 99,
                "portfolio_monthly_return": None,
            },
            {
                "date": "2026-01-30",
                "portfolio": 110,
                "portfolio_monthly_return": 0.1111111111,
            },
        ],
        "risk": {
            "metrics": {
                "annualized_return": 0.0712,
                "annualized_volatility": 0.1522,
                "sharpe_ratio": 0.94,
                "maximum_drawdown": -0.1236,
                "var_95_daily": -0.0135,
                "cvar_95_daily": -0.0195,
                "win_rate": 0.5263,
                "total_return": 0.10,
                "risk_free_annual_rate": 0.0265,
            },
            "risk_free": {"annual_rate": 0.0265},
        },
    }


def _ms_snapshot() -> dict:
    """Complete deterministic MS fixture; not sparse account observations."""
    from datetime import date, timedelta
    result=copy.deepcopy(_snapshot())
    result["subject_id"]="alpha-world-ms"
    result['reporting_currency']='HKD'
    result["period_start"]="2025-01-20"
    start=date(2025,1,20)
    end=date(2026,1,30)
    days=[]
    while start<=end:
        if start.weekday()<5:days.append(start)
        start+=timedelta(days=1)
    result["performance"]["series"]=[{"date":d.isoformat(),"currency":"HKD","nav":100*(1+.1*i/(len(days)-1))*(1+.001*math.sin(i))} for i,d in enumerate(days)]
    result["portfolio_summary"]["latest_nav"]["total_text"]=str(result["performance"]["series"][-1]["nav"])
    from ibkr_etl.hibor import period_average, NON_PUBLICATION_DATES
    from ibkr_etl.market_calendar import CALENDARS
    fixings=[{'rate_date':d.isoformat(),'annual_rate':.0265} for d in days
             if CALENDARS['XHKG'].is_session(d) and d not in NON_PUBLICATION_DATES]
    result['risk']['risk_free']=period_average(fixings,date(2025,1,20),end)
    return result


def _write_alpha_world_source(
    path: Path, *, pagesize=(595.5, 842.25), evidence_label: str = "Completed"
) -> None:
    document = canvas.Canvas(str(path), pagesize=pagesize)
    document.drawString(30, 780, "Alpha World Enhanced Index")
    document.setFont("Helvetica", 6.6)
    document.drawString(154.7, 736.0, "BASE CURRENCY")
    document.setFont("Helvetica", 9.15)
    document.drawString(154.7, 724.8, "HKD")
    document.drawString(299.4, 724.8, "HIBOR + Spread")
    document.drawString(430.9, 724.8, "Multi-Tranche")
    document.setFont("Helvetica", 12.0)
    document.drawString(390, 680, "Key Performance Indicators")
    document.drawString(30, 495, "Daily Performance Chart")
    document.drawString(30, 290, "Distribution of Annualized Returns")
    document.drawString(30, 650, f"{evidence_label} Alpha World reporting design evidence")
    document.setFont("Helvetica", 4.912386)
    document.drawString(22.947262, 28.907386, "Thisdocument is providedfor informational purposes only.")
    document.showPage()
    document.setFont("Helvetica", 12.0)
    document.drawString(30, 780, "Asset Allocation")
    document.drawString(30, 560, "Disclaimer")
    document.drawString(30, 520, f"{evidence_label} Alpha World allocation and disclosure evidence")
    document.setFont("Times-Roman", 9.0)
    document.drawString(39.7133, 516.170418, "This fact sheet relates to theWealthRyse Enhanced Index.")
    document.setFont("Helvetica", 4.912386)
    document.drawString(22.947262, 28.907386, "Thisdocument is providedfor informational purposes only.")
    document.showPage()
    document.save()

class MonthlyArtifactTests(unittest.TestCase):
    def test_normalized_nav_axis_uses_clean_ten_point_increments(self) -> None:
        minimum, maximum, ticks = _normalized_nav_axis([74.7, 91.69, 142.68])
        self.assertEqual(minimum, 70)
        self.assertEqual(maximum, 150)
        self.assertEqual(ticks, tuple(range(70, 151, 10)))

    def test_normalized_nav_axis_expands_a_flat_series(self) -> None:
        minimum, maximum, ticks = _normalized_nav_axis([100, 100])
        self.assertEqual((minimum, maximum), (90, 110))
        self.assertEqual(ticks, (90, 100, 110))

    def test_factsheet_inputs_use_point_in_time_data_and_trailing_year(self) -> None:
        inputs = factsheet_inputs(_snapshot())
        self.assertEqual(inputs.period_end, "2026-01-31")
        self.assertEqual(inputs.data_as_of, "2026-01-30")
        self.assertEqual(inputs.top_holdings[0]["symbol"], "ONE")
        self.assertAlmostEqual(inputs.trailing_twelve_month_return or 0, 110000 / 99000 - 1)
        self.assertAlmostEqual(inputs.history[1]["monthly_return"] or 0, -0.01)
        self.assertEqual(inputs.history[-1]["date"], "2026-01-30")

    def test_greater_china_factsheet_uses_account_specific_regions(self) -> None:
        snapshot = copy.deepcopy(_snapshot())
        snapshot["subject_id"] = "U16088606"
        snapshot["period_start"] = "2025-01-20"
        snapshot["holdings"]["holdings"][0]["currency"] = "TWD"
        snapshot["holdings"]["holdings"][1]["currency"] = "HKD"
        snapshot["sector_allocation"]["classifications"][0].update(
            {"sector": "Technology", "sub_sector": "Semiconductors"}
        )
        snapshot["sector_allocation"]["classifications"][1].update(
            {"sector": "Financial Services", "sub_sector": "Banks"}
        )
        inputs = factsheet_inputs(snapshot)
        self.assertEqual(inputs.top_holdings[0]["region"], "Taiwan")
        self.assertEqual(inputs.top_holdings[1]["region"], "Hong Kong")
        self.assertEqual(inputs.history_start_date, "2025-01-20")
        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory, "template.pdf")
            output = Path(directory, "factsheet.pdf")
            writer = PdfWriter()
            writer.add_blank_page(width=595.5, height=842.25)
            writer.add_blank_page(width=595.5, height=842.25)
            with template.open("wb") as stream:
                writer.write(stream)
            result = render_factsheet(
                snapshot, template_path=template, output_path=output
            )
        self.assertEqual(result["layout_version"], SUBJECT_FACTSHEET_LAYOUT_VERSION)

    def test_composite_factsheet_labels_sleeves_not_sectors(self) -> None:
        snapshot = copy.deepcopy(_snapshot())
        snapshot["subject_id"] = "composite-demo"
        snapshot["holdings"]["holdings"] = [
            {
                "conid": "hong-kong-china", "symbol": "hong-kong-china",
                "description": "China & Hong Kong", "currency": "USD",
                "asset_category": "portfolio", "direction": "long",
                "market_value_text": "91",
            },
            {
                "conid": "^HSI", "symbol": "^HSI",
                "description": "Hang Seng Price Index", "currency": "USD",
                "asset_category": "benchmark", "direction": "short",
                "market_value_text": "-49",
            },
        ]
        snapshot["sector_allocation"] = {
            "classification_basis": "composite sleeve type",
            "classifications": [], "gross_market_value": 140,
        }
        inputs = factsheet_inputs(snapshot)
        self.assertTrue(inputs.is_composite)
        self.assertEqual(inputs.top_holdings[0]["region"], "Multi-asset")
        self.assertEqual(inputs.top_holdings[1]["sub_sector"], "Short exposure")
        self.assertAlmostEqual(sum(row["weight"] for row in inputs.sectors), 1.0)
        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory, "template.pdf")
            output = Path(directory, "factsheet.pdf")
            writer = PdfWriter()
            writer.add_blank_page(width=595.5, height=842.25)
            writer.add_blank_page(width=595.5, height=842.25)
            with template.open("wb") as stream:
                writer.write(stream)
            render_factsheet(snapshot, template_path=template, output_path=output)
            text = PdfReader(output).pages[0].extract_text()
        self.assertIn("Top 5 Components", text)
        self.assertIn("Component Allocation", text)
        self.assertIn("Short exposure", text)

    def test_sector_allocation_lists_every_region_for_shared_sub_sector(self) -> None:
        snapshot = copy.deepcopy(_snapshot())
        snapshot["subject_id"] = "U16088606"
        snapshot["period_start"] = "2025-01-20"
        snapshot["holdings"]["holdings"][0]["currency"] = "TWD"
        snapshot["holdings"]["holdings"][1]["currency"] = "HKD"
        for classification in snapshot["sector_allocation"]["classifications"]:
            classification.update(
                {"sector": "Technology", "sub_sector": "Computer Hardware"}
            )

        inputs = factsheet_inputs(snapshot)

        self.assertEqual(len(inputs.sectors), 1)
        self.assertEqual(inputs.sectors[0]["region"], "Hong Kong / Taiwan")
        self.assertEqual(
            inputs.sectors[0]["region_components"], ("Hong Kong", "Taiwan")
        )
        self.assertEqual(inputs.sectors[0]["holdings"], 2)
        self.assertAlmostEqual(inputs.sectors[0]["weight"], 1.0)

        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory, "template.pdf")
            output = Path(directory, "factsheet.pdf")
            writer = PdfWriter()
            writer.add_blank_page(width=595.5, height=842.25)
            writer.add_blank_page(width=595.5, height=842.25)
            with template.open("wb") as stream:
                writer.write(stream)
            render_factsheet(snapshot, template_path=template, output_path=output)
            text = PdfReader(output).pages[0].extract_text()

        self.assertIn("Hong Kong / Taiwan", text)

    def test_collapsed_allocation_row_preserves_all_remainder_regions(self) -> None:
        holdings = []
        classifications = {}
        for index in range(22):
            conid = str(index + 1)
            currency = "HKD" if index == 20 else "TWD" if index == 21 else "USD"
            market_value = float(100 - index)
            holdings.append(
                {
                    "conid": conid,
                    "symbol": f"S{index + 1}",
                    "currency": currency,
                    "market_value_text": str(market_value),
                }
            )
            classifications[conid] = {
                "conid": conid,
                "sector": "Technology",
                "sub_sector": f"Industry {index + 1:02d}",
            }
        gross = sum(float(row["market_value_text"]) for row in holdings)

        rows = _factsheet_sector_rows(
            holdings,
            classifications,
            gross,
            subject_id="U16088606",
        )

        self.assertEqual(len(rows), 21)
        self.assertEqual(rows[-1]["sub_sector"], "Other classified holdings")
        self.assertEqual(rows[-1]["region"], "Hong Kong / Taiwan")
        self.assertEqual(rows[-1]["region_components"], ("Hong Kong", "Taiwan"))

    def test_renderer_writes_two_page_pdf_from_template(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory, "template.pdf")
            output = Path(directory, "factsheet.pdf")
            writer = PdfWriter()
            writer.add_blank_page(width=595.5, height=842.25)
            writer.add_blank_page(width=595.5, height=842.25)
            with template.open("wb") as stream:
                writer.write(stream)
            result = render_factsheet(_snapshot(), template_path=template, output_path=output)
            self.assertEqual(result["pages"], 2)
            self.assertEqual(result["layout_version"], FACTSHEET_LAYOUT_VERSION)
            self.assertEqual(len(PdfReader(output).pages), 2)
            self.assertGreater(output.stat().st_size, 1000)
            validate_factsheet_return_typography(output.read_bytes())

            runs: list[tuple[str, float, float, str | None]] = []

            def visitor(text, _cm, tm, font, font_size) -> None:
                value = text.strip()
                if value:
                    runs.append(
                        (
                            value,
                            float(tm[5]),
                            float(font_size),
                            None if font is None else str(font.get("/BaseFont", "")),
                        )
                    )

            PdfReader(output).pages[0].extract_text(visitor_text=visitor)
            positions = {(text, y, size) for text, y, size, _font in runs}
            self.assertIn(("Top 5 Products", 625.0, 7.5), positions)
            self.assertIn(("COMPANY", 610.035, 4.7), positions)
            self.assertIn(("REGION", 610.035, 4.7), positions)
            self.assertIn(("SUB-SECTOR", 610.035, 4.7), positions)
            self.assertIn(("Sector Allocation", 537.0, 7.5), positions)
            self.assertIn(("REGION", 512.435, 4.7), positions)
            self.assertIn(("SECTOR", 512.435, 4.7), positions)
            self.assertIn(("SUB-SECTOR", 512.435, 4.7), positions)
            self.assertIn(("DATE", 239.26, 5.2), positions)
            self.assertIn(
                (
                    "11.11 %",
                    473.0,
                    10.0,
                    f"/{FACTSHEET_REFERENCE_SERIF_FONT}",
                ),
                runs,
            )
            self.assertIn(
                (
                    "31 Jan 2026",
                    270.96,
                    10.0,
                    f"/{FACTSHEET_REFERENCE_SERIF_FONT}",
                ),
                runs,
            )

    def test_subject_factsheet_is_bound_to_template_and_completed_model(self) -> None:
        snapshot = _ms_snapshot()
        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory, "alpha-world-template.pdf")
            model = Path(directory, "alpha-world-completed-example.pdf")
            output = Path(directory, "alpha-world-generated.pdf")
            _write_alpha_world_source(template, evidence_label="Template")
            _write_alpha_world_source(model, evidence_label="Owner approved completed")

            model_sha256 = hashlib.sha256(model.read_bytes()).hexdigest()
            with patch(
                "ibkr_etl.monthly_artifacts.ALPHA_WORLD_MS_FACTSHEET_AUTHORITY_SHA256",
                model_sha256,
            ):
                result = render_factsheet(
                    snapshot,
                    template_path=template,
                    design_model_path=model,
                    output_path=output,
                )
            self.assertEqual(result["design_source"], "subject_completed_factsheet")
            self.assertEqual(
                result["layout_version"], ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION
            )
            self.assertEqual(result["layout_family"], "alpha-world-owner-template")
            pages = PdfReader(output).pages
            page_one = pages[0].extract_text()
            self.assertIn("Capital Path", page_one)
            self.assertIn("Asset Allocation", pages[1].extract_text())
            self.assertIn("Owner approved completed Alpha World reporting design evidence", page_one)
            self.assertNotIn("Template Alpha World reporting design evidence", page_one)
            self.assertIn("MC Annualized Return Distribution - After Interest", page_one)
            self.assertIn("(20 Jan 2025 - 30 January 2026)", page_one)
            self.assertIn("Risk-Free Rate (1M HIBOR, period average)", page_one)
            self.assertEqual(page_one.count("BASE CURRENCY"), 1)
            self.assertEqual(page_one.count("2025-03"), 2)
            self.assertNotIn("2025-01\n", page_one)
            self.assertNotIn("Top 5 Components", page_one)
            self.assertNotIn("Historical Performance", pages[1].extract_text())
            self.assertEqual(
                (PdfReader(output).metadata or {}).get("/ChartStyleVersion"),
                ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION,
            )
            self.assertEqual(
                (PdfReader(output).metadata or {}).get("/DistributionHistogramBins"),
                "40",
            )
            content = ContentStream(pages[0].get_contents(), pages[0].pdf)
            histogram_bars = []
            for operands, operator in content.operations:
                if operator != b"re" or len(operands) != 4:
                    continue
                x, y, width, height = map(float, operands)
                if (
                    113.9 <= x <= 539.0
                    and abs(y - 106.0) <= 0.05
                    and 9.5 <= width <= 11.0
                    and height >= 0.0
                ):
                    histogram_bars.append((x, y, width, height))
            self.assertEqual(len(histogram_bars), 40)
            self.assertEqual(
                result["render_contract_sha256"],
                factsheet_render_contract_sha256(
                    subject_id="alpha-world-ms",
                    supporting_template_sha256=result["supporting_template_sha256"],
                    completed_example_sha256=result["completed_example_sha256"],
                ),
            )

            with self.assertRaisesRegex(
                MonthlyArtifactError, "approved August 2026 design authority"
            ):
                render_factsheet(
                    snapshot,
                    template_path=template,
                    design_model_path=model,
                    output_path=output,
                )

            wrong_size = Path(directory, "wrong-size-model.pdf")
            wrong_canvas = canvas.Canvas(str(wrong_size), pagesize=(612, 792))
            wrong_canvas.drawString(40, 700, "Alpha World completed factsheet design model")
            wrong_canvas.showPage()
            wrong_canvas.drawString(40, 700, "Historical Performance and portfolio information")
            wrong_canvas.showPage()
            wrong_canvas.save()
            with self.assertRaisesRegex(MonthlyArtifactError, "incompatible dimensions"):
                render_factsheet(
                    snapshot,
                    template_path=template,
                    design_model_path=wrong_size,
                    output_path=output,
                )

    def test_alpha_world_t_rejects_legacy_usd_ms_template_path(self) -> None:
        snapshot = copy.deepcopy(_snapshot())
        snapshot["subject_id"] = "alpha-world-t"
        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory, "alpha-world-template.pdf")
            model = Path(directory, "alpha-world-completed-example.pdf")
            output = Path(directory, "alpha-world-t-generated.pdf")
            _write_alpha_world_source(template)
            _write_alpha_world_source(model)

            with self.assertRaisesRegex(MonthlyArtifactError, 'private-qualification'):
                render_factsheet(snapshot, template_path=template,
                    design_model_path=model, output_path=output)
            self.assertFalse(output.exists())

    def test_return_panel_rejects_legacy_font_and_overprint(self) -> None:
        for duplicate in (False, True):
            with self.subTest(duplicate=duplicate):
                buffer = io.BytesIO()
                pdf = canvas.Canvas(buffer, pagesize=(595.5, 842.25))
                pdf.setFont("Helvetica-Bold", 10)
                pdf.drawCentredString(448, 480.8, "29.27 %")
                if duplicate:
                    pdf.setFont(FACTSHEET_REFERENCE_SERIF_FONT, 10)
                    pdf.drawCentredString(448, 473, "29.27 %")
                pdf.showPage()
                pdf.showPage()
                pdf.save()
                with self.assertRaises(MonthlyArtifactError):
                    validate_factsheet_return_typography(buffer.getvalue())

    def test_future_dated_snapshot_fails_closed(self) -> None:
        snapshot = _snapshot()
        snapshot["data_as_of"] = "2026-02-01"
        with self.assertRaises(MonthlyArtifactError):
            factsheet_inputs(snapshot)

    def test_historical_performance_expands_upward_as_months_are_added(self) -> None:
        snapshot = _snapshot()
        month_rows = []
        year, month = 2025, 1
        for index in range(21):
            month_end = date(year, month, monthrange(year, month)[1])
            month_rows.append(
                {
                    "date": month_end.isoformat(),
                    "portfolio": 100 + index,
                    "portfolio_monthly_return": None if index == 0 else 0.01,
                }
            )
            month += 1
            if month == 13:
                month = 1
                year += 1
        snapshot["period_end"] = "2026-09-30"
        snapshot["data_as_of"] = "2026-09-30"
        # The portfolio history is now NAV-derived; supply all 21 actual
        # observations rather than only extending the comparison fixture.
        snapshot["performance"]["series"] = [
            {"date": "2025-01-20", "nav": 100000},
            *[{"date": row["date"], "nav": row["portfolio"] * 1000} for row in month_rows],
        ]
        snapshot["month_end_performance"] = month_rows

        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory, "template.pdf")
            output = Path(directory, "factsheet.pdf")
            writer = PdfWriter()
            writer.add_blank_page(width=595.5, height=842.25)
            writer.add_blank_page(width=595.5, height=842.25)
            with template.open("wb") as stream:
                writer.write(stream)
            render_factsheet(snapshot, template_path=template, output_path=output)

            runs: list[tuple[str, float, float]] = []

            def visitor(text, _cm, tm, _font, font_size) -> None:
                value = text.strip()
                if value:
                    runs.append((value, float(tm[5]), float(font_size)))

            PdfReader(output).pages[0].extract_text(visitor_text=visitor)
            self.assertIn(
                (
                    "Historical Performance from 20 Jan 2025 to 30 Sep 2026",
                    277.63,
                    7.8,
                ),
                runs,
            )
            self.assertIn(("DATE", 247.86, 5.2), runs)
            self.assertIn(("2026-09-30", 52.76, 5.2), runs)


class AccountChartAxisTests(unittest.TestCase):
    """Page-two chart axes for any start month, history length and growth (made-up values)."""

    @staticmethod
    def _page_two_runs(snapshot: dict) -> list[tuple[str, float, float]]:
        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory, "template.pdf")
            output = Path(directory, "factsheet.pdf")
            writer = PdfWriter()
            writer.add_blank_page(width=595.5, height=842.25)
            writer.add_blank_page(width=595.5, height=842.25)
            with template.open("wb") as stream:
                writer.write(stream)
            render_factsheet(snapshot, template_path=template, output_path=output)
            runs: list[tuple[str, float, float]] = []

            def visitor(text, cm, tm, _font, _size) -> None:
                if text.strip():
                    # Page coordinates: the date labels are drawn rotated.
                    x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
                    y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
                    runs.append((text.strip(), float(x), float(y)))

            PdfReader(output).pages[1].extract_text(visitor_text=visitor)
            return runs

    @staticmethod
    def _monthly_snapshot(first: date, months: int, growth: float = 0.01) -> dict:
        snapshot = _snapshot()
        series = [{"date": first.isoformat(), "nav": 100000}]
        year, month = first.year, first.month
        for index in range(1, months + 1):
            month += 1
            if month == 13:
                year, month = year + 1, 1
            day = date(year, month, monthrange(year, month)[1])
            series.append({"date": day.isoformat(), "nav": 100000 * (1 + growth * index)})
        snapshot["performance"]["series"] = series
        snapshot["period_end"] = snapshot["data_as_of"] = series[-1]["date"]
        return snapshot

    def test_a_history_starting_in_december_renders(self) -> None:
        runs = self._page_two_runs(self._monthly_snapshot(date(2024, 12, 16), 13))
        labels = [text for text, _x, y in runs if 594.88 - 25 < y < 594.88 - 4]
        self.assertEqual(labels[:3], ["2025", "Mar", "May"])

    def test_strong_growth_keeps_value_labels_apart(self) -> None:
        runs = self._page_two_runs(self._monthly_snapshot(date(2024, 1, 2), 30, growth=0.15))
        ticks = sorted(y for text, x, y in runs if x < 77.2 and text.isdigit())
        self.assertLessEqual(len(ticks), 11)
        self.assertGreater(min(b - a for a, b in zip(ticks, ticks[1:])), 8.0)

    def test_long_histories_thin_the_date_labels(self) -> None:
        runs = self._page_two_runs(self._monthly_snapshot(date(2020, 1, 2), 40))
        labels = [text for text, _x, y in runs if 594.88 - 25 < y < 594.88 - 4]
        self.assertEqual(labels, ["Jul", "2021", "Jul", "2022", "Jul", "2023"])


class HistoryWindowTests(unittest.TestCase):
    """Page one keeps inception plus the latest 36 month-ends (made-up values)."""

    def _page_one_text(self, months: int) -> list[str]:
        snapshot = AccountChartAxisTests._monthly_snapshot(date(2022, 1, 20), months)
        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory, "template.pdf")
            output = Path(directory, "factsheet.pdf")
            writer = PdfWriter()
            writer.add_blank_page(width=595.5, height=842.25)
            writer.add_blank_page(width=595.5, height=842.25)
            with template.open("wb") as stream:
                writer.write(stream)
            render_factsheet(snapshot, template_path=template, output_path=output)
            runs: list[str] = []
            PdfReader(output).pages[0].extract_text(
                visitor_text=lambda text, *_: runs.append(text.strip()) if text.strip() else None
            )
            return runs

    def test_up_to_36_months_shows_every_month(self) -> None:
        # The inception month counts as one of the 36: 20 Jan 2022 plus 35 month-ends.
        runs = self._page_one_text(35)
        self.assertIn("Historical Performance from 20 Jan 2022 to 31 Dec 2024", runs)
        self.assertIn("2022-02-28", runs)
        self.assertNotIn("...", runs)

    def test_longer_histories_show_inception_and_the_last_36_months(self) -> None:
        runs = self._page_one_text(54)
        self.assertIn("Historical Performance, last 36 months to 31 Jul 2026", runs)
        self.assertIn("Inception 20 Jan 2022", runs)
        self.assertIn("...", runs)
        self.assertIn("2023-08-31", runs)
        self.assertNotIn("2023-07-31", runs)
        self.assertIn("2026-07-31", runs)
        # Cumulative performance still runs from inception: 54 months of 1% steps.
        self.assertIn("54.00%", runs)


if __name__ == "__main__":
    unittest.main()
