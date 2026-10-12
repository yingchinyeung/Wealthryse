"""Local SQL correction/replay tests: never connect to production or raw Flex."""
import copy
import hashlib
import json
import unittest
import re
import shutil
import subprocess
from unittest.mock import patch

from test_subject_read_contract import Fixture
from ibkr_etl.risk_snapshots import (build_risk_snapshot, snapshot_read_status,
    risk_input_fingerprint, load_risk_inputs, RiskInputsNotReady)
from ibkr_etl.util import canonical_json


class CacheFixture(Fixture):
    def __init__(self):
        super().__init__()
        self.raw.execute('''CREATE TABLE risk_analytics_snapshots(
            tenant_id,subject_id,as_of_date,analytics_version,comparison_version,
            source_data_sha256,payload_sha256,payload_json,quality_status,generated_at,
            duration_ms,timings_json, PRIMARY KEY(tenant_id,subject_id,as_of_date,analytics_version))''')
        self.raw.execute('CREATE TABLE immutable_published_reports(id,payload)')
        self.raw.execute("INSERT INTO immutable_published_reports VALUES ('published','unchanged')")

    def execute(self, sql, params=()):
        return super().execute(sql.replace('::jsonb',''), params)

    def commit(self):self.raw.commit()
    def rollback(self):self.raw.rollback()

    def record(self, day='2025-01-22'):
        return self.execute('SELECT * FROM risk_analytics_snapshots WHERE as_of_date=?',(day,)).fetchone()


class MsCacheTests(unittest.TestCase):
    def setUp(self):
        self.c=CacheFixture()
        self.addCleanup(self.c.raw.close)

    def build(self, day='2025-01-22'):
        return build_risk_snapshot(self.c, tenant_id='tenant', subject_id='alpha-world-ms',
            as_of=day, only_if_changed=True)

    def status(self, record):
        return snapshot_read_status(record,subject_id='alpha-world-ms',as_of='2025-01-22')

    def test_real_sql_build_is_idempotent_and_never_consumes_usd_comparison(self):
        self.assertEqual(self.build()['status'],'rebuilt')
        with patch('ibkr_etl.risk_snapshots.compute_risk_snapshot') as compute:
            self.assertEqual(self.build()['status'],'unchanged')
            compute.assert_not_called()
        record=self.c.record()
        self.assertEqual(self.status(record),'current')
        payload=json.loads(record['payload_json'])
        self.assertIsNone(payload['benchmark_id'])
        self.assertAlmostEqual(payload['metrics']['total_return'],.02)
        self.assertTrue(all('performance_comparison_daily' not in sql for sql,_ in self.c.calls))
        self.assertEqual(self.c.raw.execute('SELECT payload FROM immutable_published_reports').fetchone()[0],'unchanged')
        self.assertEqual(self.c.raw.execute("SELECT total_text FROM subject_nav_series WHERE currency='USD' LIMIT 1").fetchone()[0],'999')

    def test_same_date_count_nav_correction_rebuilds_only_affected_history(self):
        # Add a fourth valid NAV so that Jan 21 is earlier than the correction.
        self.c.raw.execute("INSERT INTO subject_nav_series VALUES ('tenant','alpha-world-ms','2025-01-23','HKD','104')")
        self.c.raw.execute("INSERT INTO risk_free_rates VALUES ('tenant','2025-01-23','HKAB-HIBOR-1M','HKD','1-month',.03,'pass')")
        self.build(); self.build('2025-01-23')
        earlier=self.c.record()['source_data_sha256']; later=self.c.record('2025-01-23')['source_data_sha256']
        self.c.raw.execute("UPDATE subject_nav_series SET total_text='105' WHERE currency='HKD' AND snapshot_date='2025-01-23'")
        self.assertEqual(self.build()['status'],'unchanged')
        self.assertEqual(self.build('2025-01-23')['status'],'rebuilt')
        self.assertEqual(self.c.record()['source_data_sha256'],earlier)
        self.assertNotEqual(self.c.record('2025-01-23')['source_data_sha256'],later)

    def test_same_mean_hibor_value_corrections_invalidate_cache(self):
        self.build(); before=self.c.record(); old=json.loads(before['payload_json'])
        self.c.raw.execute("UPDATE risk_free_rates SET annual_rate=.029 WHERE rate_date='2025-01-20'")
        self.c.raw.execute("UPDATE risk_free_rates SET annual_rate=.031 WHERE rate_date='2025-01-21'")
        self.assertEqual(self.build()['status'],'rebuilt')
        after=self.c.record(); new=json.loads(after['payload_json'])
        self.assertAlmostEqual(old['risk_free']['annual_rate'],new['risk_free']['annual_rate'])
        self.assertNotEqual(before['source_data_sha256'],after['source_data_sha256'])
        self.assertNotEqual(old['risk_free']['source_value_sha256'],new['risk_free']['source_value_sha256'])

    def test_valid_hash_cannot_make_wrong_methodology_rate_or_unsupported_metrics_readable(self):
        self.build(); original=self.c.record()
        mutations=[lambda p:p.update(reporting_currency='USD'),
            lambda p:p.pop('methodology_identity'),
            lambda p:p['methodology_identity'].update(contract_sha256='0'*64),
            lambda p:p['analytics_evidence'].update(calendar_version='old'),
            lambda p:p['risk_free'].update(averaging_basis='as-of-yield'),
            lambda p:p['risk_free'].update(fixing_calendar_version='old'),
            lambda p:p['risk_free']['observations'][0].update(annual_rate=.04),
            lambda p:p['metrics'].update(risk_free_annual_rate=.042),
            lambda p:p['metrics'].update(crisis_maximum_drawdown=-.3),
            lambda p:p.update(expected_tail_loss_contributors=[]),
            lambda p:p.update(benchmark_id='GSPC'),
            lambda p:p['analytics_evidence'].update(end='2025-01-21')]
        for i,mutate in enumerate(mutations):
            record=copy.deepcopy(original); payload=json.loads(record['payload_json']); mutate(payload)
            record['payload_json']=payload
            record['payload_sha256']=hashlib.sha256(canonical_json(payload).encode()).hexdigest()
            with self.subTest(mutation=i):self.assertEqual(self.status(record),'ms_methodology_mismatch')

    def test_missing_inputs_withhold_instead_of_returning_old_cache(self):
        self.build()
        self.c.raw.execute("DELETE FROM risk_free_rates WHERE rate_date='2025-01-21'")
        with patch('ibkr_etl.risk_snapshots.compute_risk_snapshot') as compute:
            with self.assertRaises(RiskInputsNotReady):self.build()
            compute.assert_not_called()
        # Existing worker catches this failure and marks the cache fail. A
        # stored read guard alone intentionally makes no live freshness claim.
        from ibkr_etl.risk_refresh import inception_without_comparisons
        self.assertFalse(inception_without_comparisons(self.c,tenant_id='tenant',subject_id='alpha-world-ms',as_of='2025-01-22'))
        self.assertTrue(inception_without_comparisons(self.c,tenant_id='tenant',subject_id='alpha-world-ms',as_of='2025-01-20'))

    def test_calendar_change_invalidates_full_value_fingerprint(self):
        inputs,_=load_risk_inputs(self.c,tenant_id='tenant',subject_id='alpha-world-ms',as_of='2025-01-22')
        before=risk_input_fingerprint(inputs)
        with patch('ibkr_etl.risk_snapshots.CALENDAR_VERSION','new-calendar'):
            self.assertNotEqual(before,risk_input_fingerprint(inputs))

    def test_real_non_session_valuation_preserved_but_stale_history_rejected(self):
        for day,nav in [('2025-01-23','104'),('2025-01-24','103'),('2025-01-25','103.5')]:
            self.c.raw.execute('INSERT INTO subject_nav_series VALUES (?,?,?,?,?)',('tenant','alpha-world-ms',day,'HKD',nav))
            if day != '2025-01-25':
                self.c.raw.execute("INSERT INTO risk_free_rates VALUES ('tenant',?,'HKAB-HIBOR-1M','HKD','1-month',.03,'pass')",(day,))
        self.assertEqual(self.build('2025-01-25')['status'],'rebuilt')
        payload=json.loads(self.c.record('2025-01-25')['payload_json'])
        self.assertEqual(payload['analytics_evidence']['end'],'2025-01-25')
        with self.assertRaises(RiskInputsNotReady):self.build('2025-01-27')

    def test_historical_worker_marks_unready_ms_cache_failed_without_aborting(self):
        from contextlib import contextmanager
        from unittest.mock import Mock
        from ibkr_etl import risk_refresh
        self.build(); record=self.c.record()
        connection=Mock()
        connection.execute.return_value.fetchall.return_value=[{'subject_id':'alpha-world-ms','as_of_date':'2025-01-22'}]
        connection.execute.return_value.fetchone.return_value=record
        @contextmanager
        def acquired(*args,**kwargs):yield True
        with patch.object(risk_refresh,'risk_date_lock',acquired),patch.object(risk_refresh,'load_risk_inputs',side_effect=RiskInputsNotReady('Missing HIBOR fixing')),patch.object(risk_refresh,'inception_without_comparisons',return_value=False),patch.object(risk_refresh,'mark_cache_check') as mark,patch.object(risk_refresh,'build_risk_snapshot') as build:
            result=risk_refresh.audit_risk_history(connection,tenant_id='tenant',subjects=['alpha-world-ms'])
        self.assertEqual(result['blocked'],1);build.assert_not_called()
        self.assertTrue(mark.call_args.kwargs['invalidate'])
        self.assertEqual(mark.call_args.kwargs['expected_source_sha256'],record['source_data_sha256'])

    def test_ms_runtime_markup_uses_correct_cvar_and_inception_drawdown(self):
        from ibkr_etl import web
        html=web._render_account_page({'email':'owner','role':'owner'},account_id='alpha-world-ms',section='risk',
            access={'display_name':'Alpha World MS','reporting_currency':'HKD','history_start_date':'2025-01-20','preview_mode':True})
        self.assertIn('Maximum drawdown (since inception)',html)
        self.assertIn('pct(rm.cvar_95_daily)',html)
        self.assertIn('no USD-position attribution is substituted',html)

    def test_rendered_javascript_parses_and_mixed_cache_responses_are_withheld(self):
        from ibkr_etl import web
        node=shutil.which('node')
        if not node:self.skipTest('Node is not installed in this test environment')
        html=web._render_account_page({'email':'owner','role':'owner'},account_id='alpha-world-ms',section='risk',
            access={'display_name':'Alpha World MS','reporting_currency':'HKD','history_start_date':'2025-01-20','preview_mode':True})
        for code in re.findall(r'<script[^>]*>(.*?)</script>',html,re.S):
            result=subprocess.run([node,'--check','-'],input=code,text=True,encoding='utf-8',capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
        code=re.search(r'const mergeRiskResponses=.*?;\n',html,re.S).group(0)
        code+='''
            const assert=require('node:assert/strict');
            const core={cache_status:'hit',snapshot_payload_sha256:'a',metrics:{total_return:.2},risk_free:{annual_rate:.03}};
            const detail={cache_status:'hit',snapshot_payload_sha256:'a',crisis_replay:null};
            assert.equal(mergeRiskResponses(core,detail).metrics.total_return,.2);
            for(const bad of [{...detail,cache_status:'invalid'},{...detail,snapshot_payload_sha256:'b'},{}]){
                const result=mergeRiskResponses(core,bad);
                assert.deepEqual(result.metrics,{});assert.equal(result.risk_free,null);
                assert.equal(result.cache_status,'unavailable');
            }
            assert.deepEqual(mergeRiskResponses({...core,cache_status:'invalid'},detail).metrics,{});
        '''
        result=subprocess.run([node,'-'],input=code,text=True,encoding='utf-8',capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_both_website_endpoints_reject_legacy_and_read_hkd_asof_without_bootstrap(self):
        from ibkr_etl import web
        from fastapi import Response
        self.build()
        # Subquery columns used by web must exist even when there is no mapping.
        self.c.raw.execute('ALTER TABLE portfolio_benchmark_mapping ADD COLUMN tenant_id')
        with patch.object(web,'TENANT_ID','tenant'),patch.object(web,'_portfolio_access_context',return_value={'reporting_currency':'HKD'}),patch.object(web,'_database') as db:
            db.return_value.__enter__.return_value=self.c
            for endpoint in (web.risk_analytics,web.risk_analytics_details):
                response=Response()
                with patch('ibkr_etl.risk_snapshots.compute_risk_snapshot') as compute:
                    result=endpoint('alpha-world-ms',user={'email':'owner'},response=response)
                    compute.assert_not_called()
                self.assertEqual(result['cache_status'],'hit');self.assertEqual(result['as_of'],'2025-01-22')
                record=self.c.record(); payload=json.loads(record['payload_json']);payload['reporting_currency']='USD'
                self.c.execute('UPDATE risk_analytics_snapshots SET payload_json=?,payload_sha256=?',
                    (canonical_json(payload),hashlib.sha256(canonical_json(payload).encode()).hexdigest()))
                invalid=endpoint('alpha-world-ms',user={'email':'owner'},response=response)
                self.assertEqual(invalid['cache_status'],'invalid')
                self.assertEqual(response.headers['Cache-Control'],'no-store')
                self.build()


if __name__=='__main__':unittest.main()
