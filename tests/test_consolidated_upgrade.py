"""Synthetic, no-network acceptance tests for isolated upgrade changes."""
import ast
import copy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
from ibkr_etl import agent_tools as a, platform_readiness as readiness
from ibkr_etl import qualified_agent_risk as risk


class ReadinessTests(unittest.TestCase):
    def test_missing_configuration_returns_503(self):
        with patch.dict(os.environ, {'DATABASE_URL': ''}), patch.object(readiness.psycopg, 'connect') as connect:
            response = readiness.database_readiness()
        self.assertEqual(response.status_code, 503)
        connect.assert_not_called()

    def test_success_is_bounded_and_read_only(self):
        with patch.dict(os.environ, {'DATABASE_URL': 'postgresql://synthetic'}), patch.object(readiness.psycopg, 'connect') as connect:
            connection = connect.return_value.__enter__.return_value
            connection.execute.return_value.fetchone.return_value = (1,)
            response = readiness.database_readiness()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(connect.call_args.kwargs['connect_timeout'], 3)
        self.assertIn('statement_timeout=1000', connect.call_args.kwargs['options'])
        self.assertIn('default_transaction_read_only=on', connect.call_args.kwargs['options'])
        connection.execute.assert_called_once_with('SELECT 1')

    def test_failure_does_not_leak_details(self):
        with patch.dict(os.environ, {'DATABASE_URL': 'postgresql://synthetic'}), patch.object(readiness.psycopg, 'connect', side_effect=RuntimeError('SECRET:password')):
            response = readiness.database_readiness()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(b'SECRET', response.body)
        self.assertEqual(response.headers['cache-control'], 'no-store')

    def test_liveness_stays_separate(self):
        from ibkr_etl import web
        with patch.object(readiness, 'database_readiness', side_effect=AssertionError('unexpected DB call')):
            self.assertEqual(web.health(), {'status':'ok'})
        self.assertIn('/readyz', [r.path for r in web.app.routes])


class BenchmarkTests(unittest.TestCase):
    def invoke(self, subject, currency, benchmark):
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = {
            'benchmark_id':benchmark, 'reporting_currency':currency, 'license_status':'restricted'}
        connection.execute.return_value.fetchall.return_value = []
        with patch.object(a, '_effective_as_of', return_value='2026-10-07'):
            result = a._benchmark(connection, subject, None, {'benchmark_id':benchmark})
        return connection, result

    def test_ms_current_mapping_and_hkd_version(self):
        connection, result = self.invoke('alpha-world-ms','HKD','GSPC')
        calls = connection.execute.call_args_list
        self.assertEqual(calls[0].args[1], ('alpha-world-ms','GSPC','GSPC',True,'2026-10-07',True,'2026-10-07'))
        self.assertIn('CURRENT_DATE::text', calls[0].args[0])
        self.assertEqual(calls[1].args[1][2], 'benchmark-comparison-v2-ms-hkd')
        self.assertEqual(calls[1].args[1][-1], '2026-10-07')
        self.assertFalse(result.data['publication_allowed'])

    def test_t_hkd_version_and_role_not_replaced(self):
        connection, result = self.invoke('alpha-world-t','HKD','HSI')
        self.assertEqual(connection.execute.call_args.args[1][1:4], ('HSI','benchmark-comparison-v2-t-hkd','HKD'))
        self.assertEqual(result.calculation_version, 'benchmark-comparison-v2-t-hkd')

    def test_physical_keeps_historical_mapping(self):
        connection, result = self.invoke('U16088571','USD','HSI')
        self.assertFalse(connection.execute.call_args_list[0].args[1][3])
        self.assertEqual(result.calculation_version, 'benchmark-comparison-v1')

    def test_composite_usd_rejected(self):
        with self.assertRaises(a.AgentToolError):
            self.invoke('alpha-world-ms','USD','GSPC')

    def test_no_rows_remains_failure(self):
        _, result = self.invoke('alpha-world-t','HKD','HSI')
        self.assertEqual(result.quality_status, 'fail')


class QualifiedRiskTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.source = {'synthetic':True}
        self.crisis = {'maximum_drawdown':-0.4}
        self.metrics = {'annualized_return':0.1}
        self.payload = {'source_snapshot':self.source, 'crisis_replay':self.crisis,
                        'metrics':{**self.metrics,'blue_mean_daily_return':0.001}}
        self.connection.execute.return_value.fetchone.return_value = {'payload_json':json.dumps(self.payload)}

    def read(self, tenant='verified-tenant'):
        return risk.t_agent_payload(self.connection, tenant_id=tenant, as_of='2026-10-07',
            source_snapshot=self.source, core_metrics=self.metrics, crisis=self.crisis)

    def test_requires_verified_tenant(self):
        with self.assertRaises(ValueError): self.read(tenant=None)
        self.connection.execute.assert_not_called()

    def test_validated_values_and_exact_date(self):
        with patch.object(risk, 'snapshot_read_status', return_value='current'):
            self.assertEqual(self.read()['metrics']['blue_mean_daily_return'], 0.001)
        self.assertEqual(self.connection.execute.call_args.args[1][:3], ('verified-tenant','alpha-world-t','2026-10-07'))
        self.assertNotIn('<=', self.connection.execute.call_args.args[0])

    def test_bad_hash_or_stale_cache_rejected(self):
        for status in ('missing','payload_hash_mismatch','payload_identity_mismatch','t_methodology_unqualified'):
            with self.subTest(status=status), patch.object(risk,'snapshot_read_status',return_value=status), self.assertRaises(ValueError): self.read()

    def test_current_core_source_and_crisis_must_match(self):
        for field in ('source_snapshot','crisis_replay','metrics'):
            changed = copy.deepcopy(self.payload)
            changed[field] = {'wrong':True}
            self.connection.execute.return_value.fetchone.return_value = {'payload_json':json.dumps(changed)}
            with self.subTest(field=field), patch.object(risk,'snapshot_read_status',return_value='current'), self.assertRaises(ValueError): self.read()


if __name__ == '__main__':
    unittest.main()
