import hashlib
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from ibkr_etl import web
from ibkr_etl import t_factsheet_release as release
from ibkr_etl.monthly_artifacts import MonthlyArtifactError

PERIOD='2026-09-30'
SNAPSHOT={'subject_id':'alpha-world-t','reporting_currency':'HKD','period_end':PERIOD}

class Cursor:
    def __init__(self,row):self.row=row
    def fetchone(self):return self.row

class Connection:
    def __init__(self,raw,*,corrupt=False):self.raw=raw;self.corrupt=corrupt;self.statements=[]
    def execute(self,sql,args):
        self.statements.append((sql,args))
        return Cursor({'snapshot_payload_json':self.raw,'data_sha256':'a'*64 if self.corrupt else hashlib.sha256(self.raw.encode()).hexdigest(),
                       'subject_id':'alpha-world-t','period_end':PERIOD})

class TFactsheetReleaseTests(unittest.TestCase):
    def template(self,content=b'owner template'):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        path=Path(folder.name)/'owner.pdf';path.write_bytes(content)
        return patch.multiple(release,OWNER_TEMPLATE=path,AUTHORITY_SHA256=hashlib.sha256(b'owner template').hexdigest())

    def test_t_uses_its_own_contract_not_the_generic_panel(self):
        connection=Connection(json.dumps(SNAPSHOT))
        with patch.object(web,'validate_factsheet_return_typography') as generic, \
             patch.object(release,'validate_t_factsheet_release') as own:
            web._validate_release_factsheet(connection,b'pdf',subject_id='alpha-world-t',period_end=PERIOD,snapshot_id='snapshot')
        generic.assert_not_called()
        own.assert_called_once_with(b'pdf',SNAPSHOT,period_end=PERIOD)

    def test_frozen_payload_hash_is_checked_before_pdf_validation(self):
        connection=Connection(json.dumps(SNAPSHOT),corrupt=True)
        with patch.object(release,'validate_t_factsheet_release') as own:
            with self.assertRaises(MonthlyArtifactError):
                web._validate_release_factsheet(connection,b'pdf',subject_id='alpha-world-t',period_end=PERIOD,snapshot_id='snapshot')
        own.assert_not_called()
        self.assertTrue(all(sql.lstrip().startswith('SELECT') for sql,_ in connection.statements))

    def test_owner_template_and_snapshot_go_to_the_t_accuracy_check(self):
        with self.template(),patch.object(release,'validate_pdf_accuracy') as accuracy:
            release.validate_t_factsheet_release(b'pdf',SNAPSHOT,period_end=PERIOD)
        content,snapshot=accuracy.call_args.args
        self.assertEqual((content.read(),snapshot),(b'pdf',SNAPSHOT))
        self.assertEqual(accuracy.call_args.kwargs['template_path'].read(),b'owner template')

    def test_wrong_owner_template_is_refused(self):
        with self.template(b'another template'),patch.object(release,'validate_pdf_accuracy') as accuracy:
            with self.assertRaises(MonthlyArtifactError):
                release.validate_t_factsheet_release(b'pdf',SNAPSHOT,period_end=PERIOD)
        accuracy.assert_not_called()

    def test_missing_owner_template_is_refused(self):
        with patch.object(release,'OWNER_TEMPLATE',Path('/nonexistent/owner.pdf')):
            with self.assertRaises(MonthlyArtifactError):
                release.validate_t_factsheet_release(b'pdf',SNAPSHOT,period_end=PERIOD)

    def test_other_subject_or_period_is_refused(self):
        for snapshot in ({**SNAPSHOT,'subject_id':'alpha-world-ms'},{**SNAPSHOT,'reporting_currency':'USD'},{**SNAPSHOT,'period_end':'2026-08-31'}):
            with self.template(),patch.object(release,'validate_pdf_accuracy') as accuracy:
                with self.assertRaises(MonthlyArtifactError):
                    release.validate_t_factsheet_release(b'pdf',snapshot,period_end=PERIOD)
            accuracy.assert_not_called()

    def test_a_pdf_that_fails_the_accuracy_check_is_refused(self):
        with self.template():
            with self.assertRaises(Exception):
                release.validate_t_factsheet_release(b'not a PDF',SNAPSHOT,period_end=PERIOD)

if __name__=='__main__':unittest.main()
