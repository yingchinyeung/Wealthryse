import copy
import json
import sqlite3
import unittest
from dataclasses import replace
from pathlib import Path

from ibkr_etl.benchmark_analytics import ComparisonResult,ComparisonRow
import ms_website_cutover_writer as w

DAY='2026-10-08'
STAMP='2026-10-08T12:00:00+00:00'


class Result:
    def __init__(self,rows):self.rows=rows
    def fetchone(self):return self.rows[0]


class Session:
    def __init__(self):
        self.raw=sqlite3.connect(':memory:');self.raw.row_factory=sqlite3.Row
        self.raw.execute('CREATE TABLE portfolio_benchmark_mapping(tenant_id,subject_id,benchmark_id,mapping_type,effective_from,effective_to,reporting_currency,created_at,PRIMARY KEY(tenant_id,subject_id,benchmark_id,effective_from))')
        self.raw.execute('CREATE TABLE performance_comparison_daily(tenant_id,subject_id,benchmark_id,observation_date,portfolio_value,benchmark_level,normalized_portfolio,normalized_benchmark,portfolio_return,benchmark_return,excess_return,reporting_currency,calculation_version,quality_status,generated_at,PRIMARY KEY(tenant_id,subject_id,benchmark_id,observation_date,calculation_version))')
        self.raw.execute('CREATE TABLE audit_events(tenant_id,occurred_at,actor_type,actor_id,event_type,subject_id,outcome,details_json)')
        self.isolation='serializable';self.tenant=w.TENANT;self.day=DAY
    def execute(self,sql,parameters=None):
        if sql.startswith('SHOW transaction_isolation'):return Result([{'transaction_isolation':self.isolation}])
        if "current_setting('app.tenant_id')" in sql:return Result([{'tenant':self.tenant}])
        if 'CURRENT_DATE::text' in sql:return Result([{'day':self.day}])
        if 'pg_advisory_xact_lock' in sql:return Result([{}])
        return self.raw.execute(sql.replace('public.','').replace('pg_temp.','').replace('%s','?'),parameters or ())
    def snapshot(self):
        return {t:[tuple(r) for r in self.raw.execute('SELECT * FROM '+t+' ORDER BY rowid')]
            for t in ('portfolio_benchmark_mapping','performance_comparison_daily','audit_events')}


def fixture():
    rows=[dict(benchmark_id=b,mapping_type='primary' if b=='GSPC' else 'secondary',
        effective_from='2025-01-20',effective_to=None,reporting_currency='USD',created_at='original')
        for b in ('GSPC','HSI','IXIC','SHCOMP')]
    session=Session()
    for tenant,subject in ((w.TENANT,w.SUBJECT),(w.TENANT,'other-subject'),('other-tenant',w.SUBJECT)):
        for r in rows:
            session.raw.execute('INSERT INTO portfolio_benchmark_mapping VALUES(?,?,?,?,?,?,?,?)',
                (tenant,subject,*[r[k] for k in w.MAP_COLUMNS.split(',')]))
    session.raw.execute('INSERT INTO performance_comparison_daily VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (w.TENANT,w.SUBJECT,'GSPC','2025-01-20',100,100,100,100,None,None,None,'USD','benchmark-comparison-v1','warn','original'))
    session.raw.commit()
    results=[ComparisonResult(w.SUBJECT,b,'HKD',w.MS_HKD_COMPARISON_VERSION,'warn',(),
        tuple(ComparisonRow('2025-01-'+str(d),100+d,200+d,100+d,100+d,.01,.02,-.01) for d in (20,21)),{})
        for b in ('GSPC','HSI')]
    return session,{'mappings':rows},results


class ExactCutoverWriter(unittest.TestCase):
    def test_failure_is_atomic_even_if_caller_commits(self):
        c,inputs,results=fixture();before=c.snapshot()
        with self.assertRaisesRegex(RuntimeError,'Injected'):
            w.apply(c,namespace='pg_temp',inputs=inputs,results=results,day=DAY,stamp=STAMP,binding={},failure_after_rows=3)
        c.raw.commit()
        self.assertEqual(c.snapshot(),before)

    def test_apply_and_exact_rollback_preserve_history_and_other_scopes(self):
        c,inputs,results=fixture();before=c.snapshot()
        self.assertEqual(w.apply(c,namespace='public',inputs=inputs,results=results,day=DAY,stamp=STAMP,binding={}),4)
        c.raw.commit();applied=c.snapshot()
        self.assertEqual([r for r in applied['portfolio_benchmark_mapping'] if r[1]!='alpha-world-ms' or r[0]!=w.TENANT],
                         [r for r in before['portfolio_benchmark_mapping'] if r[1]!='alpha-world-ms' or r[0]!=w.TENANT])
        w.rollback(c,namespace='public',inputs=inputs,day=DAY,stamp=STAMP,binding={})
        c.raw.commit();after=c.snapshot()
        self.assertEqual(after['portfolio_benchmark_mapping'],before['portfolio_benchmark_mapping'])
        self.assertEqual(after['performance_comparison_daily'],applied['performance_comparison_daily'])
        self.assertEqual(len(after['audit_events']),2)
        self.assertTrue(all(r[2]=='agent' for r in after['audit_events']))

    def test_refuse_drift_expired_date_wrong_tenant_and_nonserializable(self):
        for fault in ('mapping','date','tenant','isolation','currency'):
            c,inputs,results=fixture()
            if fault=='mapping':c.raw.execute("UPDATE portfolio_benchmark_mapping SET reporting_currency='EUR' WHERE tenant_id=? AND subject_id=?",(w.TENANT,w.SUBJECT));c.raw.commit()
            elif fault=='date':c.day='2026-10-09'
            elif fault=='tenant':c.tenant='other-tenant'
            elif fault=='isolation':c.isolation='read committed'
            else:results[0]=replace(results[0],reporting_currency='USD')
            before=c.snapshot()
            with self.subTest(fault=fault),self.assertRaises(ValueError):
                w.apply(c,namespace='public',inputs=inputs,results=results,day=DAY,stamp=STAMP,binding={})
            self.assertEqual(c.snapshot(),before)

    def test_refuse_repeat_release_and_unknown_namespace(self):
        c,inputs,results=fixture()
        w.apply(c,namespace='public',inputs=inputs,results=results,day=DAY,stamp=STAMP,binding={})
        with self.assertRaises(ValueError):w.apply(c,namespace='public',inputs=inputs,results=results,day=DAY,stamp=STAMP,binding={})
        with self.assertRaises(ValueError):w.table('untrusted','portfolio_benchmark_mapping')
        with self.assertRaises(ValueError):w.table('public','nav_snapshots')

    def test_frozen_source_replays_qualified_native_metrics(self):
        root=Path(__file__).resolve().parents[1]/'.work/alpha-ms-website-release-20261008-v1'
        inputs=json.loads((root/'fresh-inputs-v1/inputs.json').read_bytes())
        proof=json.loads((root/'fresh-native-runtime-v1/proof.json').read_bytes())
        self.assertEqual(sum(len(r.rows) for r in w.comparisons(inputs,proof)),853)
        changed=copy.deepcopy(inputs);changed['external_flows']=[{'event_date':'2026-10-07','amount_base':1}]
        with self.assertRaises(ValueError):w.comparisons(changed,proof)
