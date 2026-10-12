import base64
import copy
import gzip
import hashlib
import sys
import unittest
from datetime import date,datetime,timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import run_specialist_daily_build as dispatch
import build_specialist_daily_report as worker
import deploy_specialist_report_worker as deployment
from ibkr_etl.util import canonical_json


class DailyBuildTests(unittest.TestCase):
    def test_only_closed_hong_kong_dates(self):
        self.assertEqual(dispatch.latest_closed(datetime(2026,10,3,9,59,tzinfo=timezone.utc)),date(2026,10,2))
        self.assertEqual(dispatch.latest_closed(datetime(2026,10,3,10,tzinfo=timezone.utc)),date(2026,10,3))
        with self.assertRaises(ValueError):dispatch.latest_closed(datetime(2026,10,3))

    def test_fixed_date_payload_preserves_installed_scope_and_no_sender(self):
        job=deployment.definition();before=copy.deepcopy(job)
        value=dispatch.template(job,date(2026,10,2))
        self.assertEqual(job,before)
        self.assertEqual(value['containers'][0]['env'],[dict(name='SPECIALIST_REPORT_DATABASE_URL',secretRef='report-url')])
        args,digest=dispatch.bundle(date(2026,10,2))
        source=gzip.decompress(base64.b64decode(args[1].split("b64decode('")[1].split("')")[0])).decode()
        compile(source,'build','exec')
        self.assertIn("REPORT_DATE='2026-10-02'",source)
        self.assertIn('persist=True',source)
        self.assertNotIn('dispatch_daily_email',source)
        self.assertNotIn('publish_website_report',(dispatch.ROOT/'scripts/build_specialist_daily_report.py').read_text())
        self.assertLess(len(args[1]),30000)
        self.assertEqual(len(digest),64)

    def test_wrong_window_delivery_or_failed_verification_cannot_pass(self):
        record=dict(status='passed',report_date='2026-10-02',database_access='verified',source_table_access=False,
            source_mutation_authority=False,database_committed=True,queued_channels=['website','email'],messages_sent=0,
            exact_replay_verified=True,fresh_login_readback_verified=True,pending_deliveries_verified=2,
            website_published=False,email_submitted=False,source_rows_changed=0,event_count=0,
            report_sha256='a'*64,message_sha256='b'*64)
        dispatch.validate_result(record,date(2026,10,2))
        for changes in ({'report_date':'2026-10-03'},{'messages_sent':1},{'database_committed':False},
                        {'exact_replay_verified':False},{'source_rows_changed':False},{'email_submitted':True}):
            with self.assertRaises(ValueError):dispatch.validate_result({**record,**changes},date(2026,10,2))

    def test_readback_checks_hash_and_unclaimed_both_channels(self):
        content=dict(report_date='2026-10-02',report={});digest=hashlib.sha256(canonical_json(content).encode()).hexdigest()
        pending=[dict(channel='email',state='pending',attempts=0),dict(channel='website',state='pending',attempts=0)]
        class C:
            def __init__(self,channels,hash_value=digest):self.channels=channels;self.hash=hash_value
            def execute(self,sql,args):
                return SimpleNamespace(fetchone=lambda:dict(report_sha256=self.hash,report_json=content),fetchall=lambda:self.channels)
        with patch.object(worker,'render_daily_message',return_value={'text':'verified'}):
            self.assertEqual(worker.check_stored(C(pending),date(2026,10,2),digest),hashlib.sha256(b'verified').hexdigest())
            for c in (C(pending,'c'*64),C(pending[:1]),C([dict(channel='email',state='accepted',attempts=1),pending[1]])):
                with self.assertRaises(ValueError):worker.check_stored(c,date(2026,10,2),digest)


if __name__=='__main__':unittest.main()
