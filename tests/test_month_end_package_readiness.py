from __future__ import annotations

import unittest
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import Mock
from reportlab.pdfgen import canvas
from pypdf import PdfReader, PdfWriter
from ibkr_etl.monthly_artifacts import (
    ALPHA_WORLD_MS_FACTSHEET_AUTHORITY_SHA256,
    ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from scripts.generate_month_end_package import (
    _completed_after_month_end,
    _require_factsheet_identity,
    _require_final_source_import,
)


class MonthEndReadinessTests(unittest.TestCase):
    def test_ms_product_title_requires_exact_account_authority_and_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "source.pdf")
            output = Path(directory, "factsheet.pdf")
            document = canvas.Canvas(str(source))
            document.drawString(40, 700, "Alpha World Enhanced Index")
            document.save()
            approved = {
                "/Title": "alpha-world-ms Factsheet - 31 August 2026",
                "/LayoutAuthoritySHA256": ALPHA_WORLD_MS_FACTSHEET_AUTHORITY_SHA256,
                "/ChartStyleVersion": ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION,
                "/LayoutFamily": "alpha-world-owner-template",
            }
            for mutation in [None, *approved]:
                metadata = dict(approved)
                if mutation:
                    metadata[mutation] = "wrong"
                writer = PdfWriter()
                writer.add_page(PdfReader(source).pages[0])
                writer.add_metadata(metadata)
                with output.open("wb") as handle:
                    writer.write(handle)
                if mutation is None:
                    _require_factsheet_identity(output, "iStrategy: Alpha World MS", subject_id="alpha-world-ms")
                    for other in ("", "alpha-world-t", "U16088571"):
                        with self.assertRaisesRegex(RuntimeError, "approved account identity"):
                            _require_factsheet_identity(output, "iStrategy: Alpha World MS", subject_id=other)
                else:
                    with self.assertRaisesRegex(RuntimeError, "approved account identity"):
                        _require_factsheet_identity(output, "iStrategy: Alpha World MS", subject_id="alpha-world-ms")

    def test_factsheet_must_show_approved_account_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "factsheet.pdf")
            document = canvas.Canvas(str(path))
            document.drawString(40, 700, "iStrategy: Greater China")
            document.save()
            _require_factsheet_identity(path, "iStrategy: Greater China")
            with self.assertRaisesRegex(RuntimeError, "approved account identity"):
                _require_factsheet_identity(path, "iStrategy: China & Hong Kong")

    def test_completion_must_be_after_month_end_in_hong_kong(self) -> None:
        end = date(2026, 8, 31)
        self.assertFalse(_completed_after_month_end("2026-08-31T15:59:59+00:00", end))
        self.assertTrue(_completed_after_month_end("2026-08-31T16:00:00+00:00", end))

    def test_physical_account_waits_for_post_month_flex_import(self) -> None:
        connection = Mock()
        connection.execute.return_value.fetchone.side_effect = [
            {"subject_type": "physical_account"},
            {"snapshot_date": "2026-08-31", "status": "success", "completed_at": "2026-08-31T15:00:00+00:00"},
            {"snapshot_date": "2026-08-31", "status": "success", "completed_at": "2026-09-01T02:00:00+00:00"},
        ]
        with self.assertRaisesRegex(RuntimeError, "post-month IBKR Flex import"):
            _require_final_source_import(connection, "U16088606", date(2026, 8, 31))

    def test_physical_account_accepts_aligned_post_month_import(self) -> None:
        connection = Mock()
        connection.execute.return_value.fetchone.side_effect = [
            {"subject_type": "physical_account"},
            {"snapshot_date": "2026-08-31", "status": "success", "completed_at": "2026-09-01T02:00:00+00:00"},
            {"snapshot_date": "2026-08-31", "status": "success", "completed_at": "2026-09-01T02:00:00+00:00"},
        ]
        _require_final_source_import(connection, "U16088606", date(2026, 8, 31))

    def test_derived_account_waits_for_validated_post_month_calculation(self) -> None:
        connection = Mock()
        connection.execute.return_value.fetchone.side_effect = [
            {"subject_type": "composite_portfolio"},
            {"day": "2026-08-31"},
            {"day": "2026-08-31"},
            None,
        ]
        with self.assertRaisesRegex(RuntimeError, "post-month derived calculation"):
            _require_final_source_import(connection, "alpha-world-ms", date(2026, 8, 31))


if __name__ == "__main__":
    unittest.main()
