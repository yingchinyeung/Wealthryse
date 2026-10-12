from datetime import date, datetime
import copy
import json
import sqlite3
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from ibkr_etl.hibor import (parse_page, fetch, period_average, refresh, latest_expected_fixing,
    expected_fixing_dates, HiborTransportError, MAX_OWNER_FALLBACK_DAYS)
from ibkr_etl.alpha_world_ms_analytics import analyze
from test_monthly_artifacts import _ms_snapshot


class HiborTests(unittest.TestCase):
    def test_official_percent_units_and_exact_one_month_field(self):
        page={'header':{'success':True},'result':{'records':[
            {'end_of_day':'2025-01-21','ir_1m':3.,'ir_3m':9.},
            {'end_of_day':'2025-01-20','ir_1m':4.,'ir_3m':8.}]}}
        self.assertEqual(parse_page(page),[{'rate_date':'2025-01-21','annual_rate':.03},
            {'rate_date':'2025-01-20','annual_rate':.04}])
        self.assertEqual(period_average(list(reversed(parse_page(page))),date(2025,1,20),date(2025,1,21))['annual_rate'],.035)

    def test_no_3m_substitution_duplicate_nan_or_unsorted_dates(self):
        base={'header':{'success':True},'result':{'records':[{'end_of_day':'2025-01-20','ir_1m':3.}]}}
        for change in (lambda p:p['header'].update(success=False),
                lambda p:p['result']['records'][0].update(ir_1m=None,ir_3m=4.),
                lambda p:p['result']['records'][0].update(ir_1m=float('nan')),
                lambda p:p['result']['records'][0].update(ir_1m=True),
                lambda p:p['result']['records'].append(dict(p['result']['records'][0]))):
            page=copy.deepcopy(base);change(page)
            with self.assertRaises(ValueError):parse_page(page)

    def test_inclusive_period_and_no_weekend_forward_fill(self):
        rows=[{'rate_date':'2025-01-24','annual_rate':.02},{'rate_date':'2025-01-27','annual_rate':.04}]
        result=period_average(rows,date(2025,1,24),date(2025,1,27))
        self.assertEqual(result['observation_count'],2)
        self.assertEqual(result['annual_rate'],.03)
        with self.assertRaises(ValueError):period_average(rows[:1],date(2025,1,24),date(2025,1,27))

    def test_weather_nonpublication_is_not_exchange_closure(self):
        rows=[{'rate_date':'2025-08-04','annual_rate':.02},{'rate_date':'2025-08-06','annual_rate':.04}]
        result=period_average(rows,date(2025,8,4),date(2025,8,6))
        self.assertEqual(result['non_publication_dates'],['2025-08-05'])
        self.assertEqual(result['annual_rate'],.03)
        with self.assertRaises(ValueError):period_average(rows[:1],date(2025,8,4),date(2025,8,6))

    def test_fetch_filters_locally_without_relying_on_date_parameters(self):
        page={'header':{'success':True},'result':{'records':[
            {'end_of_day':'2025-01-22','ir_1m':8.},{'end_of_day':'2025-01-21','ir_1m':3.},
            {'end_of_day':'2025-01-20','ir_1m':4.},{'end_of_day':'2025-01-17','ir_1m':9.}]}}
        getter=Mock(return_value=json.dumps(page).encode())
        self.assertEqual(len(fetch(date(2025,1,20),date(2025,1,21),getter=getter)),2)
        self.assertEqual(getter.call_count,1)

    def test_owner_api_fills_only_missing_published_dates(self):
        page={'header':{'success':True},'result':{'records':[
            {'end_of_day':'2025-01-20','ir_1m':4.},{'end_of_day':'2025-01-17','ir_1m':9.}]}}
        owner={'year':2025,'month':1,'day':21,'ds_is_fb':'N','1 Month':3.}
        getter=Mock(side_effect=[json.dumps(page).encode(),json.dumps(owner).encode()])
        rows=fetch(date(2025,1,20),date(2025,1,21),getter=getter)
        self.assertEqual(rows[-1],{'rate_date':'2025-01-21','annual_rate':.03})
        self.assertIn('www.hkab.org.hk/api/hibor?',getter.call_args.args[0])
        for change in ({'day':22},{'ds_is_fb':'Y'},{'1 Month':None},{'1 Month':True}):
            bad=dict(owner);bad.update(change)
            getter=Mock(side_effect=[json.dumps(page).encode(),json.dumps(bad).encode()])
            with self.assertRaises(ValueError):fetch(date(2025,1,20),date(2025,1,21),getter=getter)

    def test_refresh_requires_licence_and_complete_fetch_before_database_write(self):
        connection=Mock()
        connection.execute.return_value.fetchall.return_value=[]
        with patch('ibkr_etl.hibor.fetch') as getter:
            with self.assertRaises(ValueError):refresh(connection,date(2025,1,20),date(2025,1,21))
            getter.assert_not_called()
            connection.execute.assert_not_called()
        with patch('ibkr_etl.hibor.fetch',side_effect=ValueError('incomplete')):
            with self.assertRaises(ValueError):refresh(connection,date(2025,1,20),date(2025,1,21),license_scope_confirmed=True)
            self.assertEqual(connection.execute.call_count,1)
            self.assertTrue(connection.execute.call_args.args[0].startswith('SELECT'))
        connection.reset_mock()
        observations=[{'rate_date':'2025-01-20','annual_rate':.04},{'rate_date':'2025-01-21','annual_rate':.03}]
        with patch('ibkr_etl.hibor.fetch',return_value=observations):
            result=refresh(connection,date(2025,1,20),date(2025,1,21),license_scope_confirmed=True)
        self.assertEqual(connection.execute.call_count,3)
        connection.commit.assert_called_once()
        self.assertEqual(result['observations_loaded'],2)
        connection.reset_mock();connection.execute.side_effect=[Mock(fetchall=Mock(return_value=[])),RuntimeError('database unavailable')]
        with patch('ibkr_etl.hibor.fetch',return_value=observations):
            with self.assertRaises(RuntimeError):refresh(connection,date(2025,1,20),date(2025,1,21),license_scope_confirmed=True)
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()

    def test_http_405_uses_owner_once_per_date_without_retry(self):
        owner=lambda d:json.dumps({'year':2026,'month':10,'day':d,'ds_is_fb':'N','1 Month':2.9}).encode()
        getter=Mock(side_effect=[HTTPError('https://api.hkma.gov.hk',405,'Unavailable',None,None),owner(5),owner(6)])
        evidence={}
        rows=fetch(date(2026,10,5),date(2026,10,6),getter=getter,evidence=evidence)
        self.assertEqual([r['rate_date'] for r in rows],['2026-10-05','2026-10-06'])
        self.assertEqual(getter.call_count,3)
        self.assertEqual(evidence['hkma_requests'],1)
        self.assertEqual(evidence['hkab_requests'],2)
        self.assertEqual(evidence['immediate_retries'],0)

    def test_timeouts_and_unavailability_but_not_denied_or_malformed_data_fallback(self):
        owner=json.dumps({'year':2026,'month':10,'day':6,'ds_is_fb':'N','1 Month':2.9}).encode()
        for error in (TimeoutError(),URLError('offline'),HTTPError('https://api.hkma.gov.hk',503,'Unavailable',None,None)):
            getter=Mock(side_effect=[error,owner])
            self.assertEqual(len(fetch(date(2026,10,6),date(2026,10,6),getter=getter)),1)
            self.assertEqual(getter.call_count,2)
        getter=Mock(side_effect=HTTPError('https://api.hkma.gov.hk',403,'Denied',None,None))
        with self.assertRaises(HiborTransportError):fetch(date(2026,10,6),date(2026,10,6),getter=getter)
        self.assertEqual(getter.call_count,1)
        getter=Mock(return_value=b'<html>invalid rate payload</html>')
        with self.assertRaises(ValueError):fetch(date(2026,10,6),date(2026,10,6),getter=getter)
        self.assertEqual(getter.call_count,1)

    def test_bound_stops_before_owner_fanout_and_unverified_year_before_network(self):
        getter=Mock(side_effect=HTTPError('https://api.hkma.gov.hk',405,'Unavailable',None,None))
        self.assertGreater(len(expected_fixing_dates(date(2025,1,20),date(2025,4,1))),MAX_OWNER_FALLBACK_DAYS)
        with self.assertRaisesRegex(ValueError,'bootstrap'):fetch(date(2025,1,20),date(2025,4,1),getter=getter)
        self.assertEqual(getter.call_count,1)
        getter.reset_mock()
        with self.assertRaises(ValueError):fetch(date(2028,1,3),date(2028,1,4),getter=getter)
        getter.assert_not_called()

    def test_cached_history_reobserves_recent_week_and_current_values(self):
        cached=[{'rate_date':d.isoformat(),'annual_rate':.02} for d in expected_fixing_dates(date(2025,1,20),date(2025,4,1))]
        def getter(url,timeout):
            if 'api.hkma.gov.hk' in url:raise HTTPError(url,405,'Unavailable',None,None)
            from urllib.parse import parse_qs,urlparse
            parts={k:int(v[0]) for k,v in parse_qs(urlparse(url).query).items()}
            return json.dumps({**parts,'ds_is_fb':'N','1 Month':3.}).encode()
        evidence={}
        rows=fetch(date(2025,1,20),date(2025,4,1),getter=getter,cached_observations=cached,evidence=evidence)
        self.assertEqual(rows[0]['annual_rate'],.02)
        self.assertEqual(rows[-1]['annual_rate'],.03)
        self.assertLessEqual(evidence['hkab_requests'],6)
        self.assertEqual(evidence['hkab_dates'][0],'2025-03-25')

    def test_invalid_cached_or_owner_evidence_cannot_be_used(self):
        getter=Mock()
        for rows in ([{'rate_date':'2026-10-05','annual_rate':float('nan')}],
                     [{'rate_date':'2026-10-05','annual_rate':True}],
                     [{'rate_date':'2026-10-07','annual_rate':.02}],
                     [{'rate_date':'2026-10-05','annual_rate':.02}]*2):
            with self.assertRaises(ValueError):fetch(date(2026,10,5),date(2026,10,6),getter=getter,cached_observations=rows)
        getter.assert_not_called()
        for values in ({'ds_is_fb':'Y'},{'ds_is_fb':None},{'1 Month':None},{'day':7}):
            owner={'year':2026,'month':10,'day':6,'ds_is_fb':'N','1 Month':2.9};owner.update(values)
            getter=Mock(side_effect=[HTTPError('https://api.hkma.gov.hk',405,'Unavailable',None,None),json.dumps(owner).encode()])
            with self.assertRaises(ValueError):fetch(date(2026,10,6),date(2026,10,6),getter=getter)

    def test_publication_cutoff_is_hkt_and_respects_holidays_and_weather(self):
        for stamp,expected in (
            ('2026-10-07T15:44:59+08:00','2026-10-06'),
            ('2026-10-07T15:45:00+08:00','2026-10-07'),
            ('2026-10-07T07:00:00+00:00','2026-10-06'),
            ('2026-10-07T08:00:00+00:00','2026-10-07'),
            ('2026-10-01T18:00:00+08:00','2026-09-30'),
            ('2025-08-05T18:00:00+08:00','2025-08-04'),
            ('2027-02-09T18:00:00+08:00','2027-02-05'),
            ('2027-01-04T07:00:00+08:00','2026-12-31')):
            with self.subTest(stamp=stamp):
                self.assertEqual(latest_expected_fixing(now=datetime.fromisoformat(stamp))['expected_date'],expected)
        with self.assertRaises(ValueError):latest_expected_fixing(now=datetime(2026,10,7))

    def test_refresh_revalidates_even_mocked_fetch_before_any_insert(self):
        connection=Mock();connection.execute.return_value.fetchall.return_value=[]
        with patch('ibkr_etl.hibor.fetch',return_value=[{'rate_date':'2026-10-05','annual_rate':.02}]):
            with self.assertRaises(ValueError):refresh(connection,date(2026,10,5),date(2026,10,6),license_scope_confirmed=True)
        connection.commit.assert_not_called()
        self.assertEqual(connection.execute.call_count,1)
        self.assertTrue(connection.execute.call_args.args[0].startswith('SELECT'))

    def test_incremental_sqlite_refresh_is_idempotent_and_preserves_historical_fetch_dates(self):
        with sqlite3.connect(':memory:') as connection:
            connection.row_factory=sqlite3.Row
            connection.execute('''CREATE TABLE risk_free_rates (
                tenant_id TEXT DEFAULT 'test',rate_date TEXT,series_id TEXT,currency TEXT,tenor TEXT,
                annual_rate REAL,day_count_convention TEXT,provider TEXT,fetched_at TEXT,quality_status TEXT,
                PRIMARY KEY(tenant_id,rate_date,series_id))''')
            for day in expected_fixing_dates(date(2025,1,20),date(2025,2,3)):
                connection.execute("INSERT INTO risk_free_rates VALUES ('test',?,'HKAB-HIBOR-1M','HKD','1-month',.02,'fixing','old verified endpoint','old-fetch-date','pass')",(day.isoformat(),))
            connection.commit()
            def getter(url,timeout):
                if 'api.hkma.gov.hk' in url:raise HTTPError(url,405,'Unavailable',None,None)
                from urllib.parse import parse_qs,urlparse
                parts={k:int(v[0]) for k,v in parse_qs(urlparse(url).query).items()}
                return json.dumps({**parts,'ds_is_fb':'N','1 Month':3.}).encode()
            real_fetch=fetch
            def patched_fetch(start,end,**kwargs):return real_fetch(start,end,getter=getter,**kwargs)
            for _ in range(2):
                with patch('ibkr_etl.hibor.fetch',side_effect=patched_fetch):
                    result=refresh(connection,date(2025,1,20),date(2025,2,4),license_scope_confirmed=True)
                self.assertEqual(result['observations_loaded'],3)
                self.assertEqual(result['validated_period_observations'],9)
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM risk_free_rates').fetchone()[0],9)
                oldest=connection.execute("SELECT annual_rate,provider,fetched_at FROM risk_free_rates WHERE rate_date='2025-01-20'").fetchone()
                self.assertEqual(tuple(oldest),(.02,'old verified endpoint','old-fetch-date'))
                self.assertAlmostEqual(result['annual_rate'],(.02*6+.03*3)/9)

    def test_failed_second_upsert_rolls_back_the_first(self):
        with sqlite3.connect(':memory:') as raw:
            raw.row_factory=sqlite3.Row
            raw.execute('''CREATE TABLE risk_free_rates (
                tenant_id TEXT DEFAULT 'test',rate_date TEXT,series_id TEXT,currency TEXT,tenor TEXT,
                annual_rate REAL,day_count_convention TEXT,provider TEXT,fetched_at TEXT,quality_status TEXT,
                PRIMARY KEY(tenant_id,rate_date,series_id))''')
            class FailSecondWrite:
                def __init__(self):self.writes=0
                def execute(self,sql,params=()):
                    if sql.startswith('INSERT'):
                        self.writes+=1
                        if self.writes==2:raise RuntimeError('injected storage failure')
                    return raw.execute(sql,params)
                def commit(self):raw.commit()
                def rollback(self):raw.rollback()
            observations=[{'rate_date':'2026-10-05','annual_rate':.02},{'rate_date':'2026-10-06','annual_rate':.03}]
            with patch('ibkr_etl.hibor.fetch',return_value=observations):
                with self.assertRaises(RuntimeError):refresh(FailSecondWrite(),date(2026,10,5),date(2026,10,6),license_scope_confirmed=True)
            self.assertEqual(raw.execute('SELECT COUNT(*) FROM risk_free_rates').fetchone()[0],0)

    def test_hkd_metrics_reject_treasury_mixed_currency_and_stale_average(self):
        for change in (lambda s:s['risk']['risk_free'].update(series_id='UST-PAR-3M'),
                lambda s:s['risk']['risk_free'].update(annual_rate=.042),
                lambda s:s['performance']['series'][2].update(currency='USD'),
                lambda s:s['risk']['risk_free']['observations'].pop(2)):
            snapshot=_ms_snapshot();change(snapshot)
            with self.assertRaises(ValueError):analyze(snapshot)

    def test_source_value_hash_changes_when_fixings_change_but_mean_does_not(self):
        snapshot=_ms_snapshot()
        original=analyze(snapshot)[2]['source_value_sha256']
        rows=snapshot['risk']['risk_free']['observations']
        rows[0]['annual_rate']+=.001;rows[1]['annual_rate']-=.001
        snapshot['risk']['risk_free']=period_average(rows,date(2025,1,20),date(2026,1,30))
        self.assertNotEqual(analyze(snapshot)[2]['source_value_sha256'],original)


if __name__=='__main__':unittest.main()
