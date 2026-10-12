"""T response names only: immutable files and authorization remain unchanged."""
from contextlib import nullcontext
from unittest.mock import patch
import calendar
import unittest
from fastapi import HTTPException
from ibkr_etl import web
from test_alpha_ms_download_names import Connection, MsDownloadNameTests as Fixtures


class TDownloadNameTests(unittest.TestCase):
    def test_all_historical_and_future_names(self):
        periods = [f'{year}-{month:02d}-{calendar.monthrange(year, month)[1]}'
                   for year in (2025, 2026, 2027, 2028) for month in range(1, 13)]
        for period in periods:
            for kind, label, ext in [('factsheet','Factsheet','.pdf'),('presentation','Presentation','.pptx')]:
                self.assertEqual(web._monthly_download_filename({'report_type':kind,'filename':'opaque-hash'+ext},
                    subject_id='alpha-world-t',period_end=period),f'AlphaWorld_T_{label}_{period}{ext}')

    def test_every_download_route_preserves_bytes_hash_and_read_only_storage(self):
        fixture = Fixtures()
        for kind, label, ext in [('factsheet','Factsheet','.pdf'),('presentation','Presentation','.pptx')]:
            for route in ('review','released','onboarding'):
                with self.subTest(kind=kind,route=route):
                    row=fixture.row(kind,released=route=='released',subject='alpha-world-t')
                    if route=='onboarding':
                        row.update(container_name='internal-reports',blob_name=row['output_reference'].split('/',1)[1],
                            artifact_sha256=row['output_sha256'],status='validated',batch_status='awaiting_owner_approval')
                    connection=Connection(row)
                    with (patch.object(web,'_database',return_value=nullcontext(connection)),
                          patch.object(web,'_portfolio_access_context',return_value={}),
                          patch.object(web,'download_blob_bytes',return_value=fixture.content) as download):
                        if route=='review':response=web._download_monthly_review_report(subject_id='alpha-world-t',generation_run_id=7,user=fixture.owner)
                        elif route=='released':response=web._download_monthly_report(subject_id='alpha-world-t',artifact_release_id=9,user=fixture.viewer)
                        else:response=web.download_onboarding_historical_report('alpha-world-t',7,fixture.owner)
                    self.assertEqual(response.body,fixture.content)
                    self.assertEqual(response.headers['content-disposition'],f'attachment; filename="AlphaWorld_T_{label}_2026-09-30{ext}"')
                    self.assertEqual(response.headers['x-content-sha256'],row['output_sha256'])
                    self.assertIn(row['output_sha256'],download.call_args.args[2])
                    self.assertTrue(all(sql.lstrip().startswith('SELECT') for sql,_ in connection.statements))

    def test_invalid_date_and_type_rejected(self):
        for period in ('2026-09-29','2026-09-30\r\nX: yes','../2026-09-30','not-a-date'):
            with self.assertRaises(web.MonthlyReportError):
                web._monthly_download_filename({'report_type':'factsheet'},subject_id='alpha-world-t',period_end=period)
        with self.assertRaises(web.MonthlyReportError):
            web._monthly_download_filename({'report_type':'other'},subject_id='alpha-world-t',period_end='2026-09-30')

    def test_corruption_and_access_still_rejected(self):
        fixture=Fixtures()
        with (patch.object(web,'_database',return_value=nullcontext(Connection(fixture.row('factsheet',subject='alpha-world-t')))),
              patch.object(web,'_portfolio_access_context',return_value={}),
              patch.object(web,'download_blob_bytes',return_value=b'corrupt')):
            with self.assertRaises(HTTPException) as error:
                web._download_monthly_review_report(subject_id='alpha-world-t',generation_run_id=7,user=fixture.owner)
            self.assertEqual(error.exception.status_code,503)
        with patch.object(web,'download_blob_bytes') as download:
            with self.assertRaises(HTTPException) as error:
                web._download_monthly_review_report(subject_id='alpha-world-t',generation_run_id=7,user=fixture.viewer)
            self.assertEqual(error.exception.status_code,403)
            download.assert_not_called()
        with (patch.object(web,'_portfolio_access_context',side_effect=HTTPException(403,'Denied')),
              patch.object(web,'download_blob_bytes') as download):
            with self.assertRaises(HTTPException):
                web._download_monthly_report(subject_id='alpha-world-t',artifact_release_id=9,user=fixture.viewer)
            download.assert_not_called()


if __name__=='__main__':unittest.main()
