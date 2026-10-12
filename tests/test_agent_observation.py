import unittest
import hashlib
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from ibkr_etl.agent_governance import ObservationGrant
from ibkr_etl.agent_tools import execute_tool as deterministic_execute_tool
from ibkr_etl.agent_observation import (
    AuthorityState, Caller, ObservationBroker, ObservationDenied, observation_database,
)
from ibkr_etl.util import canonical_json

NOW = datetime(2026, 10, 2, tzinfo=timezone.utc)
TENANT = "00000000-0000-0000-0000-000000000001"


class Result:
    def __init__(self, one=None, many=()):
        self.one, self.many = one, list(many)

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.many


class Reader:
    def __init__(self):
        self.guard = {"read_only": "on", "tenant_id": TENANT, "rolsuper": False,
            "rolbypassrls": False, "statement_timeout": "15s", "lock_timeout": "2s", "isolation": "read committed"}
        self.user_active = True
        self.roles = ["viewer"]
        self.subject_available = True
        self.access = ["read"]
        self.queries = []

    def execute(self, sql, params=()):
        self.queries.append((sql, params))
        if "FROM pg_roles" in sql:
            return Result(self.guard)
        if "FROM app_users" in sql:
            return Result({"entra_object_id": "user", "email": "user@example.com",
                "display_name": "Test", "status": "active" if self.user_active else "disabled"})
        if "FROM app_user_roles" in sql:
            return Result(many=[{"role_name": role} for role in self.roles])
        if "FROM platform_subjects subject" in sql:
            return Result({"subject_id": "account-a"} if self.subject_available else None)
        if "FROM user_account_access" in sql:
            return Result(many=[{"access_level": level} for level in self.access])
        if "MAX(snapshot_date)" in sql:
            return Result({"latest": "2026-09-30"})
        if "FROM subject_nav_series" in sql:
            return Result(many=[{"snapshot_date": "2026-09-30", "currency": "USD", "total_text": "100.25"}])
        raise AssertionError("Unexpected query")


class AgentObservationTests(unittest.TestCase):
    def setUp(self):
        self.reader = Reader()
        self.open_count, self.close_count = 0, 0

        @contextmanager
        def factory(tenant_id):
            self.assertEqual(tenant_id, TENANT)
            self.open_count += 1
            try:
                yield self.reader
            finally:
                self.close_count += 1

        self.factory = factory
        self.grant = ObservationGrant(TENANT, "agent", "data_analyst",
            frozenset({"account-a"}), frozenset(),
            frozenset({"get_portfolio_summary", "get_holdings", "compare_with_benchmarks"}),
            NOW + timedelta(hours=1), enabled=True)
        self.authority = Mock()
        self.authority.load_current.return_value = AuthorityState(self.grant, False)
        self.audit=Mock()
        self.audit.record.side_effect=self.receipt
        self.broker = ObservationBroker(factory, authority=self.authority, audit=self.audit, enabled=True, clock=lambda: NOW)
        self.caller = Caller(TENANT, "agent", "user", "user@example.com")
        self.executor = patch("ibkr_etl.agent_observation.execute_tool").start()
        self.addCleanup(patch.stopall)
        self.executor.return_value = {"tool_name": "get_portfolio_summary", "subject_id": "account-a", "data": {}}

    def receipt(self,detail):
        event={key:detail[key] for key in ('operation_id','role','action','phase')}
        event.update(sequence=self.audit.record.call_count,previous_sha256='0'*64,
                     occurred_at=NOW.isoformat(),event_id=detail['operation_id']+'-'+detail['phase'],cost_microusd=0,
                     evidence_sha256=hashlib.sha256(canonical_json(detail).encode()).hexdigest())
        event['sha256']=hashlib.sha256(canonical_json(event).encode()).hexdigest()
        return {'database_committed':True,'protected_archive_verified':True,'event':event}

    def run_call(self, **kwargs):
        return self.broker.observe(kwargs.get("caller", self.caller),
            kwargs.get("tool", "get_portfolio_summary"), kwargs.get("args", {"subject_id": "account-a"}))

    def denied(self, code, **kwargs):
        with self.assertRaisesRegex(ObservationDenied, code):
            self.run_call(**kwargs)

    def test_default_disabled_and_no_database_or_authority_reads(self):
        self.broker = ObservationBroker(self.factory, authority=self.authority)
        self.denied("broker_disabled")
        self.authority.load_current.assert_not_called()
        self.assertEqual(self.open_count, 0)

    def test_missing_registered_authority_fails_closed(self):
        self.broker = ObservationBroker(self.factory, audit=self.audit, enabled=True)
        self.denied("agent_not_registered")
        self.assertEqual(self.open_count, 0)

    def test_success_rechecks_authority_and_user_and_limits_context(self):
        self.run_call()
        self.assertEqual(self.authority.load_current.call_count, 5)
        self.assertEqual(sum("FROM app_users" in sql for sql, _ in self.reader.queries), 5)
        context = self.executor.call_args.args[1]
        self.assertEqual(context.allowed_subject_ids, frozenset({"account-a"}))
        self.assertEqual(context.actor_object_id, "user")
        self.assertEqual(self.close_count, 1)

    def test_existing_deterministic_tool_integration_without_model_or_network(self):
        self.authority.load_current.return_value = AuthorityState(
            replace(self.grant, tool_names=frozenset({"get_performance_series"})), False)
        self.executor.side_effect = deterministic_execute_tool
        result = self.run_call(tool="get_performance_series")
        self.assertEqual(result["subject_id"], "account-a")
        self.assertEqual(result["quality_status"], "pass")
        self.assertEqual(result["data"]["series"], [{"date": "2026-09-30", "currency": "USD", "nav": 100.25, "flow": 0.0}])
        self.assertTrue(all(sql.strip().startswith("SELECT") for sql, _ in self.reader.queries))

    def test_agent_revocation_between_calls_is_not_cached(self):
        self.run_call()
        self.authority.load_current.return_value = AuthorityState()
        self.denied("agent_not_registered")
        self.assertEqual(self.executor.call_count, 1)

    def test_kill_switch_before_dispatch_blocks_execution(self):
        self.authority.load_current.side_effect = [AuthorityState(self.grant, False), AuthorityState(self.grant, True)]
        self.denied("kill_switch")
        self.executor.assert_not_called()
        self.assertEqual(self.close_count, 1)

    def test_revocation_during_tool_suppresses_delivery(self):
        self.authority.load_current.side_effect = [AuthorityState(self.grant, False)] * 3 + [AuthorityState()]
        self.denied("agent_not_registered")
        self.assertEqual(self.executor.call_count, 1)

    def test_user_revocation_during_tool_suppresses_delivery(self):
        def execute(*args):
            self.reader.user_active = False
            return {"tool_name": "get_portfolio_summary", "subject_id": "account-a"}
        self.executor.side_effect = execute
        self.denied("user_not_authorized")

    def test_database_isolation_must_be_proven(self):
        for field, value in (("read_only", "off"), ("tenant_id", "other"),
                ("rolsuper", True), ("rolbypassrls", True), ("statement_timeout", "0"),
                ("lock_timeout", "0"),
                ("isolation", "repeatable read")):
            with self.subTest(field=field):
                original = self.reader.guard[field]
                self.reader.guard[field] = value
                self.denied("database_isolation_unverified")
                self.reader.guard[field] = original
        self.executor.assert_not_called()

    def test_unrecognized_role_or_inactive_user_denied(self):
        self.reader.roles = ["invented-role"]
        self.denied("user_not_authorized")
        self.reader.roles = ["viewer"]
        self.reader.user_active = False
        self.denied("user_not_authorized")
        self.executor.assert_not_called()

    def test_owner_still_requires_active_subject_and_feature(self):
        self.reader.roles = ["owner"]
        self.reader.subject_available = False
        self.denied("subject_not_available")
        self.executor.assert_not_called()

    def test_user_account_entitlement_is_required(self):
        self.reader.access = ["onboard"]
        self.denied("user_scope_denied")
        self.executor.assert_not_called()

    def test_owner_cannot_expand_agent_subject_scope(self):
        self.reader.roles = ["owner"]
        self.denied("subject_scope_denied", args={"subject_id": "account-b"})
        self.assertEqual(self.open_count, 0)

    def test_user_fields_cannot_be_supplied_in_tool_arguments(self):
        for key in ("roles", "tenant_id", "user_subject_ids", "enabled", "sql", "grant"):
            with self.subTest(key=key):
                self.denied("unexpected_arguments", args={"subject_id": "account-a", key: "owner"})
        self.assertEqual(self.open_count, 0)

    def test_argument_types_dates_and_limits_are_strict(self):
        for value in (True, 0, 201, "10", None):
            self.denied("invalid_limit", tool="get_holdings", args={"subject_id": "account-a", "limit": value})
        for value in ("20261002", "2026-13-01", 20261002):
            with self.assertRaises(ObservationDenied):
                self.run_call(args={"subject_id": "account-a", "as_of": value})
        for value in ([], "", " account-a", "account-a\n"):
            self.denied("invalid_subject", args={"subject_id": value})
        self.assertEqual(self.open_count, 0)

    def test_no_shell_mutation_or_metadata_tool_on_subject_adapter(self):
        for name in ("run_shell", "execute_sql", "release_report", "inspect_resource_metadata"):
            self.denied("tool_not_registered", tool=name)
        self.executor.assert_not_called()

    def test_no_background_execution_without_interactive_identity(self):
        self.denied("authenticated_interactive_caller_required", caller=replace(self.caller, user_object_id=""))
        self.assertEqual(self.open_count, 0)

    def test_mismatched_identity_and_expired_grant_denied(self):
        self.denied("identity_mismatch", caller=replace(self.caller, tenant_id="other"))
        self.authority.load_current.return_value = AuthorityState(replace(self.grant, expires_at=NOW), False)
        self.denied("grant_expired_or_clock_invalid")
        self.executor.assert_not_called()

    def test_mismatched_result_is_not_delivered(self):
        self.executor.return_value = {"subject_id": "account-b", "tool_name": "get_portfolio_summary"}
        self.denied("result_scope_mismatch")

    def test_large_result_is_not_delivered(self):
        self.executor.return_value["data"] = "x" * 2_000_001
        self.denied("result_size_limit")

    def test_provider_failure_and_database_failure_are_sanitized(self):
        self.authority.load_current.side_effect = RuntimeError("secret-in-error")
        with self.assertLogs("uvicorn.error.ibkr_observation", level="INFO") as logs:
            self.denied("observation_unavailable")
        self.assertNotIn("secret-in-error", str(logs.output))
        self.assertNotIn("user@example.com", str(logs.output))
        self.executor.assert_not_called()

    def test_tool_failure_is_sanitized_and_connection_closed(self):
        self.executor.side_effect = RuntimeError("password=secret")
        self.denied("observation_unavailable")
        self.assertEqual(self.close_count, 1)

    def test_reader_factory_sets_transaction_local_guards(self):
        with patch("psycopg.connect") as connect:
            raw = connect.return_value.__enter__.return_value
            with observation_database("postgresql://not-a-real-server/test", TENANT):
                pass
            statements = [call.args[0] for call in raw.execute.call_args_list]
            self.assertEqual(statements[0], "SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ ONLY")
            self.assertIn("SET LOCAL statement_timeout = '15s'", statements)
            self.assertIn("SELECT set_config('app.tenant_id', %s, true)", statements)
            connect.return_value.__exit__.assert_called_once()

    def test_no_audit_adapter_means_no_tool_or_reader(self):
        self.broker.audit=None
        self.denied('protected_audit_required')
        self.executor.assert_not_called()
        self.assertEqual(self.open_count,0)

    def test_malformed_identity_or_tool_does_not_break_sanitized_logging(self):
        self.denied('authenticated_interactive_caller_required',caller=replace(self.caller,agent_principal_id=None))
        self.denied('tool_not_registered',tool={'prompt':'secret'})
        self.executor.assert_not_called()

    def test_failure_audit_outage_is_reported_without_hiding_it_in_logs(self):
        def record(detail):
            if detail['phase']=='failed': raise ConnectionError('secret provider error')
            return self.receipt(detail)
        self.audit.record.side_effect=record
        self.executor.side_effect=RuntimeError('secret tool error')
        with self.assertLogs('uvicorn.error.ibkr_observation',level='INFO') as logs:
            self.denied('audit_unavailable')
        self.assertIn('audit_unavailable',str(logs.output))
        self.assertNotIn('secret',str(logs.output))

    def test_intent_failure_prevents_tool_execution_and_no_immediate_retry(self):
        self.audit.record.side_effect=RuntimeError('secret archive details')
        self.denied('audit_unavailable')
        self.executor.assert_not_called()
        self.assertEqual(self.audit.record.call_count,1)

    def test_completion_failure_suppresses_result_without_alternate_terminal_event(self):
        def record(detail):
            if detail['phase']=='succeeded': raise ConnectionError('timeout')
            return self.receipt(detail)
        self.audit.record.side_effect=record
        self.denied('audit_unavailable')
        self.assertEqual(self.executor.call_count,1)
        self.assertEqual(self.audit.record.call_count,2)

    def test_both_phases_are_protected_and_no_raw_result_or_email_in_evidence(self):
        self.executor.return_value['data']={'private-value':'do-not-retain'}
        self.run_call()
        records=[c.args[0] for c in self.audit.record.call_args_list]
        self.assertEqual([r['phase'] for r in records],['started','succeeded'])
        self.assertEqual(records[0]['operation_id'],records[1]['operation_id'])
        self.assertFalse(records[0]['tool_attempted']);self.assertTrue(records[1]['tool_attempted'])
        self.assertTrue(all(r['result_delivery_confirmed'] is False for r in records))
        self.assertNotIn('do-not-retain',str(records));self.assertNotIn('user@example.com',str(records))

    def test_revocation_while_intent_is_archived_blocks_tool_and_records_denial(self):
        def record(detail):
            if detail['phase']=='started': self.authority.load_current.return_value=AuthorityState()
            return self.receipt(detail)
        self.audit.record.side_effect=record
        self.denied('agent_not_registered')
        self.executor.assert_not_called()
        self.assertEqual([c.args[0]['phase'] for c in self.audit.record.call_args_list],['started','denied'])

    def test_revocation_while_completion_is_archived_suppresses_delivery(self):
        def record(detail):
            if detail['phase']=='succeeded': self.reader.user_active=False
            return self.receipt(detail)
        self.audit.record.side_effect=record
        self.denied('user_not_authorized')
        self.assertEqual([c.args[0]['phase'] for c in self.audit.record.call_args_list],['started','succeeded','denied'])

    def test_forged_receipt_cannot_acknowledge_different_intent(self):
        def record(detail):
            receipt=self.receipt(detail);receipt['event']['role']='financial_analyst'
            return receipt
        self.audit.record.side_effect=record
        self.denied('audit_unavailable');self.executor.assert_not_called()

    def test_tool_failure_retains_safe_failure_code_only(self):
        self.executor.side_effect=RuntimeError('password=secret')
        self.denied('observation_unavailable')
        last=self.audit.record.call_args.args[0]
        self.assertEqual(last['phase'],'failed');self.assertEqual(last['code'],'observation_unavailable')
        self.assertNotIn('password',str(last))

    def test_deterministic_tool_with_actual_audit_handoff_and_separate_connections(self):
        from test_agent_observation_audit import Ledger,Archive,ACCESS
        from ibkr_etl.agent_observation_audit import ProtectedObservationAudit
        from ibkr_etl.agent_observation_evidence import ObservationEvidenceBinding,AGENT_PRINCIPAL
        ledger=Ledger();archive=Archive(ledger)
        @contextmanager
        def audit_connection(): yield ledger
        self.caller=replace(self.caller,agent_principal_id=AGENT_PRINCIPAL)
        self.broker.audit=ProtectedObservationAudit(audit_connection,archive,producer=ObservationEvidenceBinding('a'*64))
        self.authority.load_current.return_value=AuthorityState(
            replace(self.grant,principal_id=AGENT_PRINCIPAL,tool_names=frozenset({'get_performance_series'})),False)
        def execute(*args):
            self.assertEqual(len(ledger.events),1)
            self.assertEqual(ledger.events[0]['phase'],'started')
            self.assertEqual(len(archive.blobs),2)
            return deterministic_execute_tool(*args)
        self.executor.side_effect=execute
        with patch('ibkr_etl.agent_observation_audit.bind_audit_session'), \
             patch('ibkr_etl.specialist_evidence.OBSERVATION_MANIFESTS',frozenset({'a'*64})), \
             patch('ibkr_etl.agent_observation_audit.verify_audit_access',return_value=ACCESS):
            result=self.run_call(tool='get_performance_series')
        self.assertEqual(result['data']['series'][0]['nav'],100.25)
        self.assertEqual([e['phase'] for e in ledger.events],['started','succeeded'])
        self.assertEqual(len(archive.blobs),4)
        self.assertNotIn('100.25',str(archive.blobs))


if __name__ == "__main__":
    unittest.main()
