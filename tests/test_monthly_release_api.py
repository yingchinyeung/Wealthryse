from __future__ import annotations

import hashlib
import unittest
from contextlib import nullcontext
from unittest.mock import patch

from fastapi import HTTPException

from ibkr_etl import web


class _Cursor:
    rowcount = 1

    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row

    def fetchall(self):
        return []


class _Connection:
    def __init__(self):
        self.statements = []

    def execute(self, statement, parameters, **kwargs):
        self.statements.append((statement, parameters))
        if "SELECT release_decision_id FROM report_release_decisions" in statement:
            return _Cursor({"release_decision_id": 5})
        if 'INSERT INTO report_artifact_releases' in statement:
            return _Cursor({'artifact_release_id': 101})
        return _Cursor()


def _preview(*, approved: bool, digest: str) -> dict:
    return {
        "snapshot_id": "751c2b5a-c9cd-54fb-808e-ff98edbd8d67",
        "subject_id": "U16088606",
        "period_end": "2026-08-31",
        "package_sha256": "b" * 64,
        "selection_version": "c" * 64,
        "approved_types": ["factsheet"] if approved else [],
        "release_eligible": True,
        "release_blocker": None,
        "publication_blockers": [],
        "artifacts": [
            {
                "report_type": "factsheet",
                "generation_run_id": 81,
                "artifact_release_id": 91,
                "source_kind": "released",
                "container_name": "internal-reports",
                "blob_name": "released/U16088606/2026-08-31/factsheet.pdf",
                "sha256": digest,
                "filename": "factsheet.pdf",
                "media_type": "application/pdf",
                "approved": approved,
            },
            {"report_type": "presentation", "approved": False},
        ],
    }


def _payload(selected: list[str], *, selection_version: str = "c" * 64):
    return web.MonthlyReleaseRequest(
        snapshot_id="751c2b5a-c9cd-54fb-808e-ff98edbd8d67",
        package_sha256="b" * 64,
        selection_version=selection_version,
        selected_report_types=selected,
    )


class MonthlyReleaseApiTests(unittest.TestCase):
    user = {"email": "owner@example.com", "object_id": "owner-123", "role": "owner"}

    def test_replacement_is_content_addressed_and_withdraws_prior_versions(self):
        content=b'new validated report';digest=hashlib.sha256(content).hexdigest()
        preview=_preview(approved=False,digest=digest)
        preview['artifacts'][0]['source_kind']='review'
        preview['artifacts'][0].pop('artifact_release_id')
        connection=_Connection()
        with (
            patch.object(web,'_portfolio_access_context',return_value={'preview_mode':False}),
            patch.object(web,'_approval_database',return_value=nullcontext(connection)),
            patch.object(web,'_monthly_release_candidate',return_value=preview),
            patch.object(web,'AZURE_STORAGE_ACCOUNT_URL','https://example.blob.core.windows.net'),
            patch.object(web,'download_blob_bytes',return_value=content),
            patch.object(web,'validate_factsheet_return_typography'),
            patch.object(web,'upload_bytes',return_value=True) as upload,
        ):
            result=web.release_subject_monthly_reports('U16088606','2026-08-31',_payload(['factsheet']),self.user)
        self.assertEqual(result['newly_approved_types'],['factsheet'])
        self.assertIn(digest,upload.call_args.args[2]);self.assertFalse(upload.call_args.kwargs['overwrite'])
        sql='\n'.join(statement for statement,_ in connection.statements)
        self.assertIn('superseded_by_artifact_release_id=',sql)
        self.assertNotIn('DELETE FROM',sql)

    def test_superseded_file_cannot_be_restored(self):
        preview=_preview(approved=False,digest='a'*64);preview['artifacts'][0]['superseded']=True
        connection=_Connection()
        with (
            patch.object(web,'_portfolio_access_context',return_value={'preview_mode':False}),
            patch.object(web,'_approval_database',return_value=nullcontext(connection)),
            patch.object(web,'_monthly_release_candidate',return_value=preview),
            patch.object(web,'AZURE_STORAGE_ACCOUNT_URL','https://example.blob.core.windows.net'),
            patch.object(web,'download_blob_bytes',side_effect=AssertionError('no blob read')),
        ):
            with self.assertRaises(HTTPException) as denied:
                web.release_subject_monthly_reports('U16088606','2026-08-31',_payload(['factsheet']),self.user)
        self.assertEqual(denied.exception.status_code,409)
        self.assertFalse(any('UPDATE ' in sql for sql,_ in connection.statements))

    def test_unchecking_revokes_download_without_deleting_or_recopying_blob(self):
        connection = _Connection()
        with (
            patch.object(web, "_portfolio_access_context", return_value={"preview_mode": False}),
            patch.object(web, "_approval_database", return_value=nullcontext(connection)),
            patch.object(web, "_monthly_release_candidate", return_value=_preview(approved=True, digest="a" * 64)),
            patch.object(web, "AZURE_STORAGE_ACCOUNT_URL", "https://example.blob.core.windows.net"),
            patch.object(web, "download_blob_bytes", side_effect=AssertionError("revocation must not need a blob")),
            patch.object(web, "upload_bytes", side_effect=AssertionError("revocation must not upload")),
        ):
            result = web.release_subject_monthly_reports(
                "U16088606", "2026-08-31", _payload([]), self.user,
            )
        self.assertEqual(result["revoked_types"], ["factsheet"])
        self.assertEqual(result["approved_types"], [])
        sql = "\n".join(statement for statement, _ in connection.statements)
        self.assertIn("SET revoked_at = %s", sql)
        self.assertIn("month_end_file_approvals_changed", sql)
        self.assertNotIn("DELETE FROM", sql)

    def test_rechecking_revoked_file_requires_integrity_and_restores_entitlement(self):
        content = b"reviewed report"
        connection = _Connection()
        with (
            patch.object(web, "_portfolio_access_context", return_value={"preview_mode": False}),
            patch.object(web, "_approval_database", return_value=nullcontext(connection)),
            patch.object(web, "_monthly_release_candidate", return_value=_preview(approved=False, digest=hashlib.sha256(content).hexdigest())),
            patch.object(web, "AZURE_STORAGE_ACCOUNT_URL", "https://example.blob.core.windows.net"),
            patch.object(web, "download_blob_bytes", return_value=content),
            patch.object(web, "validate_factsheet_return_typography"),
            patch.object(web, "upload_bytes", side_effect=AssertionError("existing blob must not be copied")),
        ):
            result = web.release_subject_monthly_reports(
                "U16088606", "2026-08-31", _payload(["factsheet"]), self.user,
            )
        self.assertEqual(result["newly_approved_types"], ["factsheet"])
        sql = "\n".join(statement for statement, _ in connection.statements)
        self.assertIn("SET revoked_at = NULL", sql)

    def test_legacy_or_invalid_factsheet_cannot_be_newly_approved(self):
        content = b"not a valid approved-layout PDF"
        connection = _Connection()
        with (
            patch.object(web, "_portfolio_access_context", return_value={"preview_mode": False}),
            patch.object(web, "_approval_database", return_value=nullcontext(connection)),
            patch.object(web, "_monthly_release_candidate", return_value=_preview(approved=False, digest=hashlib.sha256(content).hexdigest())),
            patch.object(web, "AZURE_STORAGE_ACCOUNT_URL", "https://example.blob.core.windows.net"),
            patch.object(web, "download_blob_bytes", return_value=content),
            patch.object(web, "upload_bytes", side_effect=AssertionError("invalid PDF must not be released")),
        ):
            with self.assertRaises(HTTPException) as raised:
                web.release_subject_monthly_reports(
                    "U16088606", "2026-08-31", _payload(["factsheet"]), self.user,
                )
        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("approved typography", str(raised.exception.detail))
        self.assertFalse(any("INSERT INTO report_artifact_releases" in sql for sql, _ in connection.statements))

    def test_stale_checklist_is_rejected_before_any_write(self):
        connection = _Connection()
        with (
            patch.object(web, "_portfolio_access_context", return_value={"preview_mode": False}),
            patch.object(web, "_approval_database", return_value=nullcontext(connection)),
            patch.object(web, "_monthly_release_candidate", return_value=_preview(approved=True, digest="a" * 64)),
            patch.object(web, "AZURE_STORAGE_ACCOUNT_URL", "https://example.blob.core.windows.net"),
        ):
            with self.assertRaises(HTTPException) as raised:
                web.release_subject_monthly_reports(
                    "U16088606", "2026-08-31",
                    _payload([], selection_version="d" * 64), self.user,
                )
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(connection.statements, [])


if __name__ == "__main__":
    unittest.main()
