from __future__ import annotations

import unittest

from ibkr_etl.monthly_reports import (
    MonthlyReportError,
    build_monthly_report_catalog,
    validate_artifact,
    validate_period_end,
    validate_review_artifact,
)


class MonthlyReportTests(unittest.TestCase):
    def test_period_must_be_calendar_month_end(self) -> None:
        self.assertEqual(validate_period_end("2026-08-31"), "2026-08-31")
        for value in ("2026-08-30", "31/08/2026", "invalid"):
            with self.subTest(value=value), self.assertRaises(MonthlyReportError):
                validate_period_end(value)

    def test_artifact_is_restricted_to_account_month_and_container(self) -> None:
        row = {
            "artifact_release_id": 7,
            "report_type": "factsheet",
            "container_name": "internal-reports",
            "blob_name": (
                "released/U16088571/2026-08-31/"
                "U16088571_Factsheet_2026-08-31_v2.pdf"
            ),
            "artifact_sha256": "a" * 64,
        }
        artifact = validate_artifact(
            row,
            account_id="U16088571",
            period_end="2026-08-31",
            container="internal-reports",
        )
        self.assertEqual(artifact["report_type"], "factsheet")
        self.assertEqual(artifact["media_type"], "application/pdf")
        for key, value in (
            ("container_name", "other-container"),
            ("blob_name", "released/U16088606/2026-08-31/report.pdf"),
            ("artifact_sha256", "invalid"),
        ):
            invalid = dict(row)
            invalid[key] = value
            with self.subTest(key=key), self.assertRaises(MonthlyReportError):
                validate_artifact(
                    invalid,
                    account_id="U16088571",
                    period_end="2026-08-31",
                    container="internal-reports",
                )

    def test_catalog_marks_only_complete_released_package_ready(self) -> None:
        snapshot = {
            "snapshot_id": "snapshot-1",
            "period_end": "2026-08-31",
            "data_as_of": "2026-08-31",
            "quality_status": "warn",
        }
        base = {
            "snapshot_id": "snapshot-1",
            "period_end": "2026-08-31",
            "container_name": "internal-reports",
            "artifact_sha256": "b" * 64,
        }
        artifacts = [
            {
                **base,
                "artifact_release_id": 1,
                "report_type": "factsheet",
                "blob_name": "released/U16088571/2026-08-31/factsheet.pdf",
            },
            {
                **base,
                "artifact_release_id": 2,
                "report_type": "presentation",
                "blob_name": "released/U16088571/2026-08-31/presentation.pptx",
            },
        ]
        catalog = build_monthly_report_catalog(
            [snapshot],
            [
                {
                    "snapshot_id": "snapshot-1",
                    "report_type": "factsheet",
                    "status": "released_internal",
                },
                {
                    "snapshot_id": "snapshot-1",
                    "report_type": "presentation",
                    "status": "released_internal",
                },
            ],
            artifacts,
            account_id="U16088571",
            container="internal-reports",
        )
        self.assertTrue(catalog[0]["ready"])
        self.assertEqual(catalog[0]["status"], "ready")
        self.assertEqual(
            catalog[0]["artifacts"][0]["download_url"],
            "/api/accounts/U16088571/reporting/artifacts/1/download",
        )

    def test_catalog_exposes_complete_review_package_without_marking_released(self) -> None:
        snapshot = {
            "snapshot_id": "snapshot-1",
            "period_end": "2025-01-31",
            "data_as_of": "2025-01-31",
            "quality_status": "warn",
        }
        runs = []
        for run_id, report_type, filename in (
            (10, "factsheet", "factsheet.pdf"),
            (11, "presentation", "presentation.pptx"),
        ):
            runs.append(
                {
                    "generation_run_id": run_id,
                    "snapshot_id": "snapshot-1",
                    "period_end": "2025-01-31",
                    "report_type": report_type,
                    "status": "awaiting_approval",
                    "output_reference": (
                        "internal-reports/review/U16088571/2025-01-31/" + filename
                    ),
                    "output_sha256": "c" * 64,
                }
            )
        catalog = build_monthly_report_catalog(
            [snapshot],
            runs,
            [],
            account_id="U16088571",
            container="internal-reports",
        )
        month = catalog[0]
        self.assertFalse(month["ready"])
        self.assertTrue(month["review_ready"])
        self.assertTrue(month["downloadable"])
        self.assertEqual(month["status"], "review_ready")
        self.assertEqual(
            month["artifacts"][0]["download_url"],
            "/api/accounts/U16088571/reporting/review-artifacts/10/download",
        )

    def test_catalog_supports_one_released_file_and_one_pending_file(self) -> None:
        snapshot = {
            "snapshot_id": "snapshot-1", "period_end": "2026-08-31",
            "data_as_of": "2026-08-31", "quality_status": "pass",
        }
        runs = [
            {
                "generation_run_id": 10, "snapshot_id": "snapshot-1",
                "period_end": "2026-08-31", "report_type": "factsheet",
                "status": "released_internal",
                "output_reference": "internal-reports/review/U16088571/2026-08-31/factsheet.pdf",
                "output_sha256": "a" * 64,
            },
            {
                "generation_run_id": 11, "snapshot_id": "snapshot-1",
                "period_end": "2026-08-31", "report_type": "presentation",
                "status": "awaiting_approval",
                "output_reference": "internal-reports/review/U16088571/2026-08-31/presentation.pptx",
                "output_sha256": "b" * 64,
            },
        ]
        released = [{
            "artifact_release_id": 3, "snapshot_id": "snapshot-1",
            "period_end": "2026-08-31", "report_type": "factsheet",
            "container_name": "internal-reports",
            "blob_name": "released/U16088571/2026-08-31/factsheet.pdf",
            "artifact_sha256": "a" * 64,
        }]
        month = build_monthly_report_catalog(
            [snapshot], runs, released,
            account_id="U16088571", container="internal-reports",
        )[0]
        self.assertFalse(month["ready"])
        self.assertTrue(month["partially_released"])
        self.assertEqual(month["status"], "partially_released")
        self.assertEqual(len(month["released_artifacts"]), 1)
        self.assertEqual(len(month["review_artifacts"]), 2)

    def test_catalog_rejects_any_cross_portfolio_record(self) -> None:
        snapshot = {
            "snapshot_id": "snapshot-1",
            "subject_id": "U16088571",
            "period_end": "2026-08-31",
            "data_as_of": "2026-08-31",
            "quality_status": "pass",
        }
        with self.assertRaisesRegex(
            MonthlyReportError, "snapshot belongs to another portfolio"
        ):
            build_monthly_report_catalog(
                [snapshot],
                [],
                [],
                account_id="U16088606",
                container="internal-reports",
            )

        run = {
            "generation_run_id": 10,
            "snapshot_id": "snapshot-2",
            "subject_id": "U16088571",
            "period_end": "2026-08-31",
            "report_type": "factsheet",
            "status": "awaiting_approval",
            "output_reference": (
                "internal-reports/review/U16088606/2026-08-31/factsheet.pdf"
            ),
            "output_sha256": "e" * 64,
        }
        with self.assertRaisesRegex(
            MonthlyReportError, "run belongs to another portfolio"
        ):
            build_monthly_report_catalog(
                [],
                [run],
                [],
                account_id="U16088606",
                container="internal-reports",
            )

    def test_review_artifact_is_restricted_to_exact_review_prefix(self) -> None:
        row = {
            "generation_run_id": 10,
            "report_type": "factsheet",
            "output_reference": (
                "internal-reports/review/U16088571/2025-01-31/factsheet.pdf"
            ),
            "output_sha256": "d" * 64,
        }
        artifact = validate_review_artifact(
            row,
            account_id="U16088571",
            period_end="2025-01-31",
            container="internal-reports",
        )
        self.assertTrue(artifact["review_only"])
        self.assertEqual(
            artifact["blob_name"],
            "review/U16088571/2025-01-31/factsheet.pdf",
        )
        invalid = dict(row)
        invalid["output_reference"] = (
            "internal-reports/released/U16088571/2025-01-31/factsheet.pdf"
        )
        with self.assertRaises(MonthlyReportError):
            validate_review_artifact(
                invalid,
                account_id="U16088571",
                period_end="2025-01-31",
                container="internal-reports",
            )


if __name__ == "__main__":
    unittest.main()
