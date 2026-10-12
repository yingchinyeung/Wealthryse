"""Candidate-only read-only Azure acceptance; no models, data writes or approvals."""
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sys
import time
import unittest

os.environ['PGOPTIONS'] = '-c default_transaction_read_only=on -c statement_timeout=120000'
os.environ['PORTAL_TEST_RUNTIME'] = '/app'
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.path[:0] = ['/app/src', '/app/scripts']
from ibkr_etl import web
from ibkr_etl.agent_tools import AgentToolContext, execute_tool
from ibkr_etl.platform_readiness import database_readiness
from fastapi.testclient import TestClient

REQUIRED = ('annualized_return', 'annualized_volatility', 'risk_free_annual_rate',
            'sharpe_ratio', 'expected_maximum_drawdown_1y', 'actual_maximum_drawdown_1y',
            'tail_maximum_drawdown_1y', 'crisis_maximum_drawdown', 'var_95_daily',
            'daily_expected_tail_loss', 'win_rate', 'blue_mean_daily_return')


def finite(value):
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def main():
    suite = unittest.defaultTestLoader.discover('/app/upgrade-tests', pattern='test_snapshot_access_correction.py')
    results = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
    client = TestClient(web.app)
    statuses = [client.get('/readyz').status_code]
    saved_url = os.environ['DATABASE_URL']
    began = time.monotonic()
    try:
        # Refuse a connection to an unused local port; never interrupt the live DB.
        os.environ['DATABASE_URL'] = 'postgresql://unavailable@127.0.0.1:1/unavailable'
        response = client.get('/readyz')
        statuses.append(response.status_code)
        assert response.json() == {'status': 'not_ready'}
        assert client.get('/healthz').json() == {'status': 'ok'}
    finally:
        os.environ['DATABASE_URL'] = saved_url
    elapsed = time.monotonic() - began
    statuses.append(client.get('/readyz').status_code)
    records = []
    with web._database() as raw:
        assert raw.execute("SELECT current_setting('transaction_read_only') AS value").fetchone()['value'] == 'on'
        subjects = raw.execute("SELECT subject_id FROM platform_subjects WHERE tenant_id=%s AND lifecycle_status='active' ORDER BY subject_id", (web.TENANT_ID,)).fetchall()
        for subject in subjects:
            sid = subject['subject_id']
            context = AgentToolContext(web.TENANT_ID, 'upgrade-readonly-acceptance', frozenset({sid}), 'upgrade-readonly')
            calls = [('get_risk_metrics', {}) , ('compare_with_benchmarks', {})]
            if sid in ('alpha-world-ms', 'alpha-world-t'):
                calls += [('compare_with_benchmarks', {'benchmark_id': b}) for b in ('GSPC', 'HSI')]
            for tool, extra in calls:
                try:
                    value = execute_tool(web._portable_database_connection(raw), context, tool, {'subject_id': sid, **extra})
                    data = value.get('data') or {}
                    metrics = data.get('metrics') or {}
                    records.append({'subject': sid, 'tool': tool, 'requested': extra,
                        'quality': value.get('quality_status'), 'as_of': value.get('as_of'),
                        'missing_metrics': [key for key in REQUIRED if not finite(metrics.get(key))] if tool == 'get_risk_metrics' else [],
                        'series_count': len(data.get('series') or []),
                        'benchmark_id': (data.get('benchmark') or {}).get('benchmark_id'),
                        'reporting_currency': data.get('reporting_currency'),
                        'publication_allowed': data.get('publication_allowed'),
                        'calculation_version': value.get('calculation_version'),
                        'message': data.get('message')})
                except Exception as error:
                    records.append({'subject': sid, 'tool': tool, 'requested': extra,
                                    'error_class': type(error).__name__, 'error_code': getattr(error, 'code', None)})
    composite = [r for r in records if r['subject'] in ('alpha-world-ms', 'alpha-world-t') and r['tool'] == 'compare_with_benchmarks']
    t_risk = [r for r in records if r['subject'] == 'alpha-world-t' and r['tool'] == 'get_risk_metrics']
    passed = (results.wasSuccessful() and results.testsRun == 14 and statuses == [200, 503, 200]
              and elapsed < 5 and len(composite) == 6
              and all(r.get('series_count', 0) > 0 and r.get('reporting_currency') == 'HKD'
                      and r.get('quality') in ('pass', 'warn') for r in composite)
              and len(t_risk) == 1 and t_risk[0].get('quality') in ('pass', 'warn')
              and not t_risk[0].get('missing_metrics'))
    files = ('web.py', 'agent_tools.py', 'platform_readiness.py', 'qualified_agent_risk.py')
    record = {'read_only': True, 'database_writes': 0, 'model_calls': 0,
              'candidate_changed_paths_passed': passed, 'all_portfolios_qualified': False,
              'synthetic_access_tests': results.testsRun, 'access_tests_passed': results.wasSuccessful(),
              'readiness_statuses': statuses, 'isolated_connection_failure_seconds': round(elapsed, 3),
              'readiness_tested_through_http_routes': True,
              'records': records, 'source_sha256': {n: hashlib.sha256((Path('/app/src/ibkr_etl') / n).read_bytes()).hexdigest() for n in files}}
    print('PLATFORM_CORRECTION_PROBE ' + json.dumps(record, default=str), flush=True)
    if not passed:
        raise SystemExit('Candidate runtime qualification failed')


if __name__ == '__main__':
    main()
