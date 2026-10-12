"""Synthetic-only negative and positive authorization regression checks."""
import hashlib
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

FOLDER = Path(__file__).resolve().parents[1]
ROOT = FOLDER.parents[1] if len(FOLDER.parents) > 1 else FOLDER
BASE = (Path(os.environ['PORTAL_TEST_RUNTIME']) if os.environ.get('PORTAL_TEST_RUNTIME') else
        FOLDER if (FOLDER / 'src/ibkr_etl').is_dir() else
        ROOT / '.work/alpha-t-download-names-20261009-v2/web/local-runtime')
sys.path.insert(0, str(BASE / 'src'))
import ibkr_etl
if (FOLDER / 'runtime/src/ibkr_etl').is_dir():
    ibkr_etl.__path__.insert(0, str(FOLDER / 'runtime/src/ibkr_etl'))
from ibkr_etl import web
from fastapi import HTTPException
from fastapi.testclient import TestClient

OWNER = {'role': 'owner', 'email': 'synthetic@example.invalid', 'object_id': 'synthetic', 'authorization_source': 'database'}
SUBJECT = 'alpha-world-t'
ACCESS = {'display_name': 'Alpha World T', 'reporting_currency': 'HKD', 'history_start_date': '2025-01-20', 'preview_mode': False}


class SnapshotAccessTests(unittest.TestCase):
    def setUp(self):
        self.payload = json.dumps({'subject_id': SUBJECT, 'synthetic': True})
        self.row = {'subject_id': SUBJECT, 'period_end': '2026-09-30', 'status': 'frozen',
                    'snapshot_payload_json': self.payload, 'data_sha256': hashlib.sha256(self.payload.encode()).hexdigest()}
        self.database = self.enterContext(patch.object(web, '_database'))
        self.connection = self.database.return_value.__enter__.return_value
        self.connection.execute.return_value.fetchone.return_value = self.row
        self.guard = self.enterContext(patch.object(web, '_portfolio_access_context', return_value=ACCESS))

    def test_non_owners_denied_before_database_or_subject_lookup(self):
        for role in ('viewer', 'analyst', 'platform_admin', 'onboarding_operator', None):
            with self.subTest(role=role), self.assertRaises(HTTPException) as error:
                web.export_reporting_snapshot('latest', user={**OWNER, 'role': role}, subject_id=SUBJECT)
            self.assertEqual(error.exception.status_code, 403)
        self.database.assert_not_called()
        self.guard.assert_not_called()

    def test_latest_owner_query_binds_selected_subject(self):
        result = web.export_reporting_snapshot('latest', True, OWNER, SUBJECT)
        self.guard.assert_called_once_with(SUBJECT, OWNER)
        self.assertEqual(self.connection.execute.call_args.args[1], (web.TENANT_ID, SUBJECT))
        self.assertIn('alpha-world-t-2026-09-30', result.headers['content-disposition'])
        self.assertEqual(result.body.decode(), self.payload)

    def test_explicit_owner_query_binds_subject_and_uuid(self):
        sid = '342e82f3-15b7-532b-83b5-663f1b3e6570'
        web.export_reporting_snapshot(sid, user=OWNER, subject_id=SUBJECT)
        self.assertEqual(self.connection.execute.call_args.args[1], (web.TENANT_ID, sid, SUBJECT))

    def test_wrong_subject_row_rejected_even_if_database_returns_it(self):
        self.row['subject_id'] = 'U16088571'
        with self.assertRaises(HTTPException) as error:
            web.export_reporting_snapshot('latest', user=OWNER, subject_id=SUBJECT)
        self.assertEqual(error.exception.status_code, 404)

    def test_unavailable_subject_guard_stops_query(self):
        self.guard.side_effect = HTTPException(404, 'Portfolio not available')
        with self.assertRaises(HTTPException):
            web.export_reporting_snapshot('latest', user=OWNER, subject_id=SUBJECT)
        self.database.assert_not_called()

    def test_invalid_subject_rejected_before_database(self):
        for subject in ('bad\r\nname', 'bad/name', '../pilot', '', 'x' * 65):
            with self.subTest(subject=subject), self.assertRaises(HTTPException) as error:
                web.export_reporting_snapshot('latest', user=OWNER, subject_id=subject)
            self.assertEqual(error.exception.status_code, 400)
        self.database.assert_not_called()

    def test_invalid_uuid(self):
        with self.assertRaises(HTTPException) as error:
            web.export_reporting_snapshot('not-a-uuid', user=OWNER, subject_id=SUBJECT)
        self.assertEqual(error.exception.status_code, 400)
        self.database.assert_not_called()

    def test_integrity_failure_is_preserved(self):
        self.row['data_sha256'] = '0' * 64
        with self.assertRaises(HTTPException) as error:
            web.export_reporting_snapshot('latest', user=OWNER, subject_id=SUBJECT)
        self.assertEqual(error.exception.status_code, 503)

    def test_missing_row(self):
        self.connection.execute.return_value.fetchone.return_value = None
        with self.assertRaises(HTTPException) as error:
            web.export_reporting_snapshot('latest', user=OWNER, subject_id=SUBJECT)
        self.assertEqual(error.exception.status_code, 404)

    def test_owner_without_download_gets_no_attachment_header(self):
        response = web.export_reporting_snapshot('latest', user=OWNER, subject_id=SUBJECT)
        self.assertNotIn('content-disposition', response.headers)

    def test_owner_workspace_link_is_scoped(self):
        html = web._render_account_page(OWNER, account_id=SUBJECT, section='reports', access=ACCESS)
        self.assertIn('download=true&amp;subject_id=alpha-world-t', html)
        self.assertIn('Owner-only frozen snapshot JSON', html)

    def test_viewer_and_preview_workspaces_have_no_snapshot_link(self):
        for user, access in (({**OWNER, 'role': 'viewer'}, ACCESS), (OWNER, {**ACCESS, 'preview_mode': True})):
            html = web._render_account_page(user, account_id=SUBJECT, section='reports', access=access)
            self.assertNotIn('id="export-reporting-snapshot"', html)

    def test_http_dependency_denies_viewer(self):
        with patch.object(web, '_authorized_user', return_value={**OWNER, 'role': 'viewer'}):
            response = TestClient(web.app).get('/api/reporting/snapshots/latest.json?subject_id=alpha-world-t')
        self.assertEqual(response.status_code, 403)
        self.database.assert_not_called()

    def test_http_owner_can_download_only_selected_subject(self):
        with patch.object(web, '_authorized_user', return_value=OWNER):
            response = TestClient(web.app).get('/api/reporting/snapshots/latest.json?subject_id=alpha-world-t&download=true')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['subject_id'], SUBJECT)
        self.assertEqual(response.headers['cache-control'], 'no-store')


if __name__ == '__main__':
    unittest.main(verbosity=2)
