import hashlib
import json
import asyncio
import shutil
import subprocess
import unittest
from contextlib import contextmanager, ExitStack
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException
from starlette.requests import Request
from ibkr_etl import web
from ibkr_etl.agent_daily_inbox import verified_content, publish_website_report, list_daily_reports, get_daily_report
from ibkr_etl.agent_daily_inbox_ui import DAILY_INBOX_SCRIPT
from ibkr_etl.agent_daily_schedule import report_window
from ibkr_etl.agent_governance import REGISTRY
from ibkr_etl.specialist_service_access import grant_statements, ROLES
from ibkr_etl.util import canonical_json

DAY = date(2026, 10, 1)
TENANT = '00000000-0000-0000-0000-000000000001'
OWNER = dict(role='owner', authorization_source='database', object_id='owner', email='owner@example.com')


def report_row():
    start, end = report_window(DAY)
    content = dict(report_date=DAY.isoformat(), timezone='Asia/Hong_Kong', period_start=start.isoformat(),
                   period_end=end.isoformat(), report=dict(ledger_slice_verified=True, protected_archive_verified=True,
                   event_count=0, role_counts={role: 0 for role in REGISTRY}))
    return dict(report_date=DAY, report_json=content, report_sha256=hashlib.sha256(canonical_json(content).encode()).hexdigest())


class Connection:
    autocommit = True
    info = SimpleNamespace(transaction_status=0)
    def __init__(self, row=None): self.row = row; self.commits = 0; self.calls = []
    @contextmanager
    def transaction(self):
        yield
        self.commits += 1
    def execute(self, sql, values=()):
        self.calls.append((sql, values))
        row = {'tenant_id': TENANT} if 'app_tenant_id()' in sql else self.row
        return SimpleNamespace(fetchone=lambda: row, fetchall=lambda: row)


class DailyInboxTests(unittest.TestCase):
    def test_verified_content_rejects_corrupt_hash_and_date(self):
        self.assertIn('No specialist actions', verified_content(report_row())['text'])
        for changed in ({'report_sha256': 'f'*64}, {'report_date': date(2026,10,2)}):
            with self.assertRaises(ValueError): verified_content(dict(report_row(), **changed))

    def test_publication_commits_atomically_and_does_not_claim_read(self):
        c = Connection(report_row())
        with ExitStack() as stack:
            mocks = {name: stack.enter_context(patch('ibkr_etl.agent_daily_inbox.'+name))
                     for name in ('claim_delivery', 'start_send', 'finish_send')}
            mocks['claim_delivery'].return_value = dict(report_date=DAY, lease_token='token')
            mocks['finish_send'].side_effect = lambda *args, **kw: self.assertEqual(c.commits, 0)
            result = publish_website_report(c, tenant_id=TENANT)
            self.assertEqual(c.commits, 1)
            self.assertFalse(result['owner_read_confirmed'])
            self.assertEqual(mocks['finish_send'].call_args.kwargs['outcome'], 'delivered')
            self.assertEqual(mocks['claim_delivery'].call_args.kwargs['channel'], 'website')

    def test_invalid_publication_rolls_back_without_delivery(self):
        for fail in ('integrity', 'finish'):
            c = Connection(report_row())
            if fail == 'integrity': c.row['report_sha256'] = 'bad'
            with patch('ibkr_etl.agent_daily_inbox.claim_delivery', return_value=dict(report_date=DAY,lease_token='t')), \
                 patch('ibkr_etl.agent_daily_inbox.start_send'), \
                 patch('ibkr_etl.agent_daily_inbox.finish_send', side_effect=ValueError('lease lost')) as finish:
                with self.assertRaises(ValueError): publish_website_report(c, tenant_id=TENANT)
                self.assertEqual(c.commits, 0)
                if fail == 'integrity': finish.assert_not_called()

    def test_idle_or_open_transaction(self):
        c = Connection()
        with patch('ibkr_etl.agent_daily_inbox.claim_delivery', return_value=None):
            self.assertEqual(publish_website_report(c, tenant_id=TENANT), {'state':'idle'})
        c.autocommit = False
        with self.assertRaises(ValueError): publish_website_report(c, tenant_id=TENANT)

    def test_exact_publication_rejects_another_day_or_hash_before_delivery(self):
        for day, digest in ((date(2026,10,2),report_row()['report_sha256']), (DAY,'f'*64)):
            c = Connection(report_row())
            with patch('ibkr_etl.agent_daily_inbox.claim_delivery', return_value=dict(report_date=DAY,lease_token='t')), \
                 patch('ibkr_etl.agent_daily_inbox.start_send') as start, \
                 patch('ibkr_etl.agent_daily_inbox.finish_send') as finish:
                with self.assertRaises(ValueError):
                    publish_website_report(c,tenant_id=TENANT,report_date=day,report_sha256=digest)
                self.assertEqual(c.commits,0); start.assert_not_called(); finish.assert_not_called()

    def test_exact_publication_validates_target_before_claim(self):
        for fields in ({'report_date':DAY}, {'report_sha256':'a'*64},
                       {'report_date':'2026-10-01','report_sha256':'a'*64},
                       {'report_date':DAY,'report_sha256':'bad'}):
            with patch('ibkr_etl.agent_daily_inbox.claim_delivery') as claim:
                with self.assertRaises(ValueError): publish_website_report(Connection(),tenant_id=TENANT,**fields)
                claim.assert_not_called()

    def test_exact_publication_accepts_only_bound_report(self):
        row=report_row(); c=Connection(row)
        with patch('ibkr_etl.agent_daily_inbox.claim_delivery',return_value=dict(report_date=DAY,lease_token='t')), \
             patch('ibkr_etl.agent_daily_inbox.start_send'), patch('ibkr_etl.agent_daily_inbox.finish_send'):
            self.assertEqual(publish_website_report(c,tenant_id=TENANT,report_date=DAY,
                report_sha256=row['report_sha256'])['report_date'],DAY.isoformat())
            self.assertEqual(c.commits,1)

    def test_listing_bounded_and_published_only(self):
        c = Connection([dict(report_date=DAY,report_sha256='a'*64,published_at=datetime.now(timezone.utc),email_state='accepted')])
        result = list_daily_reports(c,tenant_id=TENANT)
        self.assertEqual(result[0]['email_state'], 'accepted')
        self.assertNotIn('report_json', result[0])
        self.assertIn("d.state='delivered'", c.calls[-1][0])
        for limit in (True,0,91,'30'):
            with self.assertRaises(ValueError): list_daily_reports(c,tenant_id=TENANT,limit=limit)
        with self.assertRaises(ValueError): list_daily_reports(c,tenant_id='other')

    def test_unpublished_detail_not_returned_and_hash_rechecked(self):
        c = Connection()
        self.assertIsNone(get_daily_report(c,tenant_id=TENANT,report_date=DAY))
        self.assertIn("d.state='delivered'", c.calls[-1][0])
        c.row = report_row()
        self.assertEqual(get_daily_report(c,tenant_id=TENANT,report_date=DAY)['report_date'],DAY.isoformat())
        c.row['report_sha256'] = 'bad'
        with self.assertRaises(ValueError): get_daily_report(c,tenant_id=TENANT,report_date=DAY)


class InboxWebTests(unittest.TestCase):
    def test_http_inbox_revocation_rechecks_owner(self):
        async def get():
            from httpx import ASGITransport, AsyncClient
            # A real transport coordinates request completion/disconnection.
            # Repeated synthetic http.request messages can hang middleware on
            # Windows. Keep the same HTTP authorization/revocation assertions.
            async with AsyncClient(transport=ASGITransport(app=web.app), base_url='https://test') as client:
                response = await asyncio.wait_for(client.get('/api/ai-operations/daily-reports'),10)
                return response.status_code
        with patch.object(web,'AUTHORIZATION_MODE','database'), patch.object(web,'_database_authorized_user',
                side_effect=[OWNER,dict(OWNER,role='viewer'),None]) as auth, patch.object(web,'_database') as db:
            self.assertEqual([asyncio.run(get()) for _ in range(3)],[410,403,403])
            self.assertEqual(auth.call_count,3); db.assert_not_called()

    @unittest.skipUnless(shutil.which('node'), 'Node runtime unavailable')
    def test_javascript_parses_in_real_runtime(self):
        source=DAILY_INBOX_SCRIPT.removeprefix('\n<script>\n').removesuffix('</script>\n')
        result=subprocess.run([shutil.which('node'),'--check'],input=source,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_authorization_precedes_database_access(self):
        for user in ({},dict(OWNER,role='viewer'),dict(OWNER,authorization_source='emergency_owner_fallback')):
            with patch.object(web,'_database') as db:
                with self.assertRaises(HTTPException): web.daily_report_inbox(Request({'type':'http'}),user)
                with self.assertRaises(HTTPException): web.daily_report_detail('2026-10-01',Request({'type':'http'}),user)
                db.assert_not_called()

    def test_retired_inbox_never_accesses_database(self):
        with patch.object(web,'_database',side_effect=RuntimeError('secret database URL')) as db:
            result = web.daily_report_inbox(Request({'type':'http'}),OWNER)
        self.assertEqual(result.status_code,410)
        self.assertEqual(result.headers['cache-control'],'no-store')
        self.assertNotIn(b'secret',result.body)
        db.assert_not_called()

    def test_retired_detail_never_reads_or_deletes_historical_evidence(self):
        with patch.object(web,'_database') as db:
            for day in ('20261001','bad','2026-02-30','2026-10-01'):
                result=web.daily_report_detail(day,Request({'type':'http'}),OWNER)
                self.assertEqual(result.status_code,410)
                self.assertEqual(result.headers['cache-control'],'no-store')
            db.assert_not_called()

    def test_inbox_exposes_only_readonly_polling(self):
        for value in ('textContent','replaceChildren','AbortController','document.hidden','pagehide','120000',
                      'response.status === 403', "cache: 'no-store'", 'delivery unconfirmed'):
            self.assertIn(value,DAILY_INBOX_SCRIPT)
        for value in ('innerHTML','localStorage','POST','eval('): self.assertNotIn(value,DAILY_INBOX_SCRIPT)
        with self.assertRaises(HTTPException) as stopped:
            web._render_platform_page(OWNER,section='ai-operations')
        self.assertEqual(stopped.exception.status_code,404)


class ServiceAccessTests(unittest.TestCase):
    def test_distinct_narrow_capabilities(self):
        self.assertEqual(len(set(ROLES.values())),3)
        reader = ' '.join(grant_statements('reader'))
        self.assertNotIn('INSERT',reader); self.assertNotIn('UPDATE',reader); self.assertNotIn('audit_events',reader)
        audit = ' '.join(grant_statements('audit'))
        self.assertNotIn('daily_reports',audit); self.assertNotIn('UPDATE',audit)
        report = ' '.join(grant_statements('report'))
        self.assertIn('UPDATE (state,lease_token',report)
        for forbidden in ('DELETE','TRUNCATE','ALL PRIVILEGES','LOGIN','WITH GRANT OPTION'):
            for role in ROLES: self.assertNotIn(forbidden,' '.join(grant_statements(role)))

    def test_unregistered_capability_or_schema_rejected(self):
        for role,schema in [('owner','public'),('reader','public; DROP SCHEMA public')]:
            with self.assertRaises(ValueError): grant_statements(role,schema=schema)
        self.assertTrue(all(statement.endswith(ROLES['reader']+'_trial') for statement in
                            grant_statements('reader',schema='specialist_report_builder_trial')))
