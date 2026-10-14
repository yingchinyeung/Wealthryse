"""Stage 3's composite report run: which months, when a month is ready, and the design's files.

The run itself, against PostgreSQL, is in test_broker_onboarding_db.py.
"""
import hashlib
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from ibkr_etl import composite_reports as cr


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class _Answers:
    """Answers each query with the rows listed for the first table it names."""

    def __init__(self, **tables):
        self.tables = tables

    def execute(self, statement, parameters=()):
        table = next(name for name in self.tables if name in statement)
        return _Rows(self.tables[table])


def _ready(nav="2025-02-28", components="2025-02-28", completed="2025-03-01T02:00:00+00:00"):
    return cr.month_end_ready(_Answers(
        subject_nav_series=[{"day": nav}], synthetic_portfolio_components=[{"day": components}],
        subject_calculation_runs=[{"completed_at": completed}] if completed else []), "synthetic", "2025-02-28")


class MonthTests(unittest.TestCase):
    def test_every_month_end_after_inception_up_to_the_last_completed_month(self):
        self.assertEqual(cr.report_months(date(2025, 1, 20), date(2025, 3, 3)), ["2025-01-31", "2025-02-28"])
        self.assertEqual(cr.report_months(date(2025, 1, 31), date(2025, 3, 1)), ["2025-02-28"])
        self.assertEqual(cr.report_months(date(2025, 12, 15), date(2026, 1, 31)), ["2025-12-31"])
        self.assertEqual(cr.report_months(date(2025, 3, 3), date(2025, 3, 31)), [])

    def test_a_month_is_ready_once_its_month_end_calculation_has_run(self):
        self.assertTrue(_ready())
        self.assertTrue(_ready(nav="2025-02-26", components="2025-02-26"))
        # Calculated on 28 February in Hong Kong is still inside the month.
        self.assertFalse(_ready(completed="2025-02-28T15:00:00+00:00"))
        self.assertTrue(_ready(completed="2025-02-28T16:30:00+00:00"))
        self.assertFalse(_ready(completed=None))
        self.assertFalse(_ready(components="2025-02-27"))
        self.assertFalse(_ready(nav="2025-02-20", components="2025-02-20"))
        self.assertFalse(_ready(nav=None))

    def test_the_check_says_what_was_rendered_and_what_follows(self):
        reports = [{"period_end": period, "report_type": kind} for period in ("2025-01-31", "2025-02-28")
                   for kind in cr.KINDS]
        self.assertEqual(cr._message({"design": "alpha-world-t", "reports": reports, "waiting_for": None}),
                         ("pass", "Factsheets for 2 months from 2025-01-31 to 2025-02-28 in the Alpha World "
                                  "T design, each read back and checked."))
        self.assertEqual(cr._message({"design": "alpha-world-ms", "reports": reports[:1], "waiting_for": "2025-02-28"}),
                         ("pass", "A factsheet for 2025-01-31 in the Alpha World MS design, each read back "
                                  "and checked. 2025-02-28 follows once its month-end calculation has run."))
        self.assertEqual(cr._message({"design": "alpha-world-ms", "reports": [], "waiting_for": None}),
                         ("warn", "No month has ended since inception, so there are no reports to review yet."))
        self.assertEqual(cr._message({"design": "alpha-world-ms", "reports": [], "waiting_for": "2025-01-31"}),
                         ("pending", "Waiting for the 2025-01-31 month-end calculation before rendering."))


class DesignFileTests(unittest.TestCase):
    def assets(self, style, template=b"template", completed=b"completed", roles=("template", "completed_example")):
        content = {"template": template, "completed_example": completed}
        rows = [{"asset_role": role, "container_name": "designs", "blob_name": f"{style}/{role}.pdf",
                 "sha256": hashlib.sha256(content[role]).hexdigest()} for role in roles]
        return _Answers(subject_report_template_assets=rows), {f"{style}/{role}.pdf": content[role] for role in roles}

    def test_ms_renders_from_its_completed_factsheet_with_its_template_as_support(self):
        connection, blobs = self.assets("alpha-world-ms")
        authority = hashlib.sha256(b"completed").hexdigest()
        with patch.dict(cr.DESIGNS["alpha-world-ms"], {"authority": authority}), \
                tempfile.TemporaryDirectory() as folder:
            assets = cr.design_assets(connection, "alpha-world-ms")
            self.assertEqual(assets.template_version,
                             f"sha256:{authority}+supporting-sha256:{hashlib.sha256(b'template').hexdigest()}"
                             f"+layout:{cr.LAYOUT_VERSION}")
            sources = cr.fetch_sources(assets, Path(folder), fetch=lambda container, blob: blobs[blob])
            self.assertEqual((sources["template_path"].read_bytes(), sources["design_model_path"].read_bytes()),
                             (b"template", b"completed"))
            blobs["alpha-world-ms/template.pdf"] = b"changed"
            with self.assertRaisesRegex(cr.CompositeReportError, "approved template failed its check"):
                cr.fetch_sources(assets, Path(folder), fetch=lambda container, blob: blobs[blob])

    def test_t_renders_from_its_one_owner_template(self):
        connection, blobs = self.assets("alpha-world-t", template=b"owner", completed=b"owner")
        authority = hashlib.sha256(b"owner").hexdigest()
        with patch.dict(cr.DESIGNS["alpha-world-t"], {"authority": authority}), \
                tempfile.TemporaryDirectory() as folder:
            assets = cr.design_assets(connection, "alpha-world-t")
            self.assertEqual(assets.template_version, f"sha256:{authority}+layout:{cr.LAYOUT_VERSION}")
            sources = cr.fetch_sources(assets, Path(folder), fetch=lambda container, blob: blobs[blob])
            self.assertIsNone(sources["design_model_path"])
            two, _ = self.assets("alpha-world-t", template=b"other", completed=b"owner")
            with self.assertRaisesRegex(cr.CompositeReportError, "one source"):
                cr.design_assets(two, "alpha-world-t")

    def test_missing_or_unapproved_design_files_are_refused(self):
        connection, _ = self.assets("alpha-world-ms", roles=("template",))
        with self.assertRaisesRegex(cr.CompositeReportError, "no approved factsheet template"):
            cr.design_assets(connection, "alpha-world-ms")
        connection, _ = self.assets("alpha-world-ms")
        with self.assertRaisesRegex(cr.CompositeReportError, "not its approved authority"):
            cr.design_assets(connection, "alpha-world-ms")


if __name__ == "__main__":
    unittest.main()
