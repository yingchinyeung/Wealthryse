from __future__ import annotations

import hashlib
import unittest
from contextlib import nullcontext
from unittest.mock import patch

from fastapi import HTTPException
from ibkr_etl import web


class Connection:
    def __init__(self, row):
        self.row = row
        self.statements = []

    def execute(self, sql, args):
        self.statements.append((sql, args))
        return self

    def fetchone(self):
        return self.row


class MsDownloadNameTests(unittest.TestCase):
    owner = {"role": "owner", "object_id": "owner-123"}
    viewer = {"role": "viewer", "object_id": "viewer-123"}
    content = b"Original immutable report bytes"

    def row(self, kind, *, released=False, subject="alpha-world-ms"):
        digest = hashlib.sha256(self.content).hexdigest()
        extension = ".pdf" if kind == "factsheet" else ".pptx"
        filename = "original_2026-09-30_" + digest + extension
        row = {"report_type": kind, "subject_id": subject, "period_end": "2026-09-30",
               "snapshot_id": "snapshot-1", "generation_run_id": 7,
               "status": "released_internal" if released else "awaiting_approval",
               "output_sha256": digest,
               "output_reference": "internal-reports/review/" + subject + "/2026-09-30/" + filename,
               "artifact_release_id": 9 if released else None}
        if released:
            row.update(container_name="internal-reports", blob_name="released/" + subject + "/2026-09-30/" + filename,
                       artifact_sha256=digest)
        return row

    def test_names_use_validated_period_and_kind_not_storage_filename(self):
        for period in ("2025-01-31", "2026-09-30", "2026-10-31", "2028-02-29"):
            for kind, label, extension in (("factsheet", "Factsheet", "pdf"), ("presentation", "Presentation", "pptx")):
                with self.subTest(period=period, kind=kind):
                    artifact = {"report_type": kind, "filename": "opaque_" + "a" * 64 + "." + extension}
                    self.assertEqual(web._monthly_download_filename(artifact, subject_id="alpha-world-ms", period_end=period),
                                     f"AlphaWorld_MS_{label}_{period}.{extension}")

    def test_other_accounts_keep_existing_names(self):
        name = "existing_" + "a" * 64 + ".pdf"
        for subject in ("unrelated-portfolio", "U16088571", "hong-kong-china"):
            self.assertEqual(web._monthly_download_filename({"filename": name, "report_type": "factsheet"},
                             subject_id=subject, period_end="2026-09-30"), name)

    def test_unsafe_or_non_month_end_period_is_rejected(self):
        for period in ("2026-09-29", "2026-09-30\r\nX-Injected: yes", "../2026-09-30", "not-a-date"):
            with self.subTest(period=period), self.assertRaises(web.MonthlyReportError):
                web._monthly_download_filename({"report_type": "factsheet"}, subject_id="alpha-world-ms", period_end=period)

    def test_unknown_type_is_rejected(self):
        with self.assertRaises(web.MonthlyReportError):
            web._monthly_download_filename({"report_type": "other"}, subject_id="alpha-world-ms", period_end="2026-09-30")

    def test_review_and_released_downloads_only_change_response_name(self):
        for kind, label, extension in (("factsheet", "Factsheet", "pdf"), ("presentation", "Presentation", "pptx")):
            for released in (False, True):
                with self.subTest(kind=kind, released=released):
                    row = self.row(kind, released=released)
                    connection = Connection(row)
                    with (patch.object(web, "_database", return_value=nullcontext(connection)),
                          patch.object(web, "_portfolio_access_context", return_value={}),
                          patch.object(web, "download_blob_bytes", return_value=self.content) as download):
                        response = (web._download_monthly_report(subject_id="alpha-world-ms", artifact_release_id=9, user=self.viewer)
                                    if released else web._download_monthly_review_report(subject_id="alpha-world-ms", generation_run_id=7, user=self.owner))
                    self.assertEqual(response.body, self.content)
                    self.assertEqual(response.headers["content-disposition"], f'attachment; filename="AlphaWorld_MS_{label}_2026-09-30.{extension}"')
                    self.assertEqual(response.headers["x-content-sha256"], row["output_sha256"])
                    self.assertEqual(response.headers["cache-control"], "private, no-store")
                    self.assertIn(row["output_sha256"], download.call_args.args[2])
                    self.assertTrue(all(sql.lstrip().startswith("SELECT") for sql, _ in connection.statements))

    def test_corrupt_bytes_still_fail_closed(self):
        for released in (False, True):
            with self.subTest(released=released):
                connection = Connection(self.row("factsheet", released=released))
                with (patch.object(web, "_database", return_value=nullcontext(connection)),
                      patch.object(web, "_portfolio_access_context", return_value={}),
                      patch.object(web, "download_blob_bytes", return_value=b"changed")):
                    with self.assertRaises(HTTPException) as denied:
                        if released:
                            web._download_monthly_report(subject_id="alpha-world-ms", artifact_release_id=9, user=self.viewer)
                        else:
                            web._download_monthly_review_report(subject_id="alpha-world-ms", generation_run_id=7, user=self.owner)
                self.assertEqual(denied.exception.status_code, 503)

    def test_historical_onboarding_downloads_keep_hash_and_bytes(self):
        for kind, label, extension in (("factsheet", "Factsheet", "pdf"), ("presentation", "Presentation", "pptx")):
            with self.subTest(kind=kind):
                row = self.row(kind)
                row.update(container_name="internal-reports", blob_name=row["output_reference"].split("/", 1)[1],
                           artifact_sha256=row["output_sha256"], status="validated", batch_status="approved")
                connection = Connection(row)
                with (patch.object(web, "_database", return_value=nullcontext(connection)),
                      patch.object(web, "download_blob_bytes", return_value=self.content) as download):
                    response = web.download_onboarding_historical_report("alpha-world-ms", 7, self.owner)
                self.assertEqual(response.body, self.content)
                self.assertEqual(response.headers["content-disposition"], f'attachment; filename="AlphaWorld_MS_{label}_2026-09-30.{extension}"')
                self.assertEqual(response.headers["x-content-sha256"], row["output_sha256"])
                self.assertIn(row["output_sha256"], download.call_args.args[2])
                self.assertTrue(all(sql.lstrip().startswith("SELECT") for sql, _ in connection.statements))

    def test_owner_review_still_rejects_viewers_without_reading_files(self):
        with patch.object(web, "download_blob_bytes") as download:
            with self.assertRaises(HTTPException) as denied:
                web._download_monthly_review_report(subject_id="alpha-world-ms", generation_run_id=7, user=self.viewer)
        self.assertEqual(denied.exception.status_code, 403)
        download.assert_not_called()

    def test_subject_permissions_still_checked_before_released_read(self):
        with (patch.object(web, "_portfolio_access_context", side_effect=HTTPException(403, "Denied")),
              patch.object(web, "download_blob_bytes") as download):
            with self.assertRaises(HTTPException) as denied:
                web._download_monthly_report(subject_id="alpha-world-ms", artifact_release_id=9, user=self.viewer)
        self.assertEqual(denied.exception.status_code, 403)
        download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
