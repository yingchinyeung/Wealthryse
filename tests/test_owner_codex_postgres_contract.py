"""Static PostgreSQL contract guards, NOT a live migration/RLS acceptance test.

This file never connects, applies a migration, provisions a role, or grants any
permission. A disposable PostgreSQL test environment with a separately scoped
non-superuser/NOBYPASSRLS schema owner is required for live acceptance. The
existing production readers/workers must not be broadened to run these tests.
"""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/postgres/059_owner_codex_tasks.sql"
SERVICE = ROOT / "src/ibkr_etl/owner_codex_tasks.py"
TABLES = ("owner_codex_sessions", "owner_codex_tasks", "owner_codex_task_events")


def statements():
    return re.sub(r"--[^\n]*", "", MIGRATION.read_text(encoding="utf-8"))


def body(name):
    match = re.search(r"CREATE FUNCTION " + name + r"\(\) RETURNS trigger LANGUAGE plpgsql AS \$\$(.*?)\$\$;",
                      statements(), re.S)
    if match is None:
        raise AssertionError("Expected exact reviewed trigger function")
    return match.group(1)


class OwnerCodexPostgresContractTests(unittest.TestCase):
    def test_migration_is_metadata_only_and_does_not_provision_authority(self):
        sql = statements()
        self.assertEqual(tuple(re.findall(r"CREATE TABLE ([a-z_]+)", sql)), TABLES)
        self.assertNotRegex(sql, r"\bGRANT\b|\bCREATE\s+(?:ROLE|USER|DATABASE|SCHEMA)\b|SECURITY\s+DEFINER")
        self.assertNotRegex(sql, r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+(?:public\.)?(?:app_users|app_user_roles|user_account_access|schema_migrations)\b")
        self.assertNotRegex(sql, r"\b(?:nav_history|position_snapshots|reporting_snapshots|flex_sources|raw_statements)\b")

    def test_every_relation_forces_both_tenant_and_exact_owner_isolation(self):
        sql = statements()
        scope = "tenant_id=app_tenant_id() AND owner_object_id::text=current_setting('app.owner_object_id',true)"
        for table in TABLES:
            self.assertIn(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;", sql)
            self.assertIn(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;", sql)
            policy = re.search(r"CREATE POLICY owner_isolation ON " + table + r"\s+(.*?);", sql, re.S).group(1)
            self.assertEqual(policy.count(scope), 2)
            self.assertEqual(policy.count("tenant_id::text=current_setting('app.tenant_id',true)"), 2)
            self.assertIn("USING (", policy)
            self.assertIn("WITH CHECK (", policy)
            self.assertNotIn(" OR ", policy)

    def test_composite_keys_bind_sessions_tasks_and_events_to_owner_and_tenant(self):
        sql = statements()
        for key in ("PRIMARY KEY (tenant_id,owner_object_id,session_id)",
                    "PRIMARY KEY (tenant_id,owner_object_id,task_id)",
                    "PRIMARY KEY (tenant_id,owner_object_id,task_id,event_no)"):
            self.assertIn(key, sql)
        self.assertEqual(sql.count("UNIQUE (tenant_id,owner_object_id,idempotency_key)"), 2)
        for relation in ("FOREIGN KEY (tenant_id,owner_object_id,session_id)",
                         "FOREIGN KEY (tenant_id,owner_object_id,task_id)"):
            self.assertIn(relation, sql)

    def test_task_and_every_status_revision_are_atomic_at_commit(self):
        sql = statements()
        self.assertIn("NEW.revision<>OLD.revision+1", body("guard_owner_codex_task"))
        self.assertIn("NEW.event_no<>current_task.revision", body("guard_owner_codex_event"))
        self.assertIn("NEW.state<>current_task.state", body("guard_owner_codex_event"))
        self.assertIn("CREATE CONSTRAINT TRIGGER owner_codex_revision_event AFTER INSERT OR UPDATE ON owner_codex_tasks", sql)
        self.assertIn("DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION guard_owner_codex_event_presence()", sql)
        self.assertIn("event_no=$4 AND state=$5", body("guard_owner_codex_event_presence"))

    def test_trigger_queries_cannot_select_search_path_decoy_tables(self):
        task = body("guard_owner_codex_task")
        event = body("guard_owner_codex_event")
        presence = body("guard_owner_codex_event_presence")
        self.assertIn("format('SELECT %I.app_tenant_id()', TG_TABLE_SCHEMA)", task)
        self.assertIn("NEW.tenant_id::text IS DISTINCT FROM current_setting('app.tenant_id',true)", task)
        self.assertIn("FROM %I.owner_codex_tasks", event)
        self.assertIn("TG_TABLE_SCHEMA)", event)
        self.assertIn("INTO current_task USING NEW.tenant_id,NEW.owner_object_id,NEW.task_id", event)
        self.assertIn("FROM %I.owner_codex_task_events", presence)
        self.assertIn("TG_TABLE_SCHEMA)", presence)
        # EXECUTE does not set PL/pgSQL FOUND: the row must be checked directly.
        self.assertIn("current_task.revision IS NULL", event)
        self.assertNotIn("IF NOT FOUND", event)

    def test_history_cannot_be_rewritten_or_removed(self):
        sql = statements()
        self.assertIn("owner_codex_sessions_immutable BEFORE UPDATE OR DELETE OR TRUNCATE", sql)
        self.assertIn("owner_codex_events_append_only BEFORE UPDATE OR DELETE OR TRUNCATE", sql)
        self.assertIn("owner_codex_tasks_no_delete BEFORE DELETE OR TRUNCATE", sql)
        self.assertIn("RAISE EXCEPTION", body("guard_owner_codex_append_only"))
        task = body("guard_owner_codex_task")
        for field in ("tenant_id", "owner_object_id", "task_id", "session_id", "idempotency_key",
                      "task_kind", "subject_ids", "input_sha256", "request_sha256", "authority_sha256", "created_at"):
            self.assertIn("NEW." + field, task)
            self.assertIn("OLD." + field, task)

    def test_nullable_error_cannot_pass_failed_state_check_as_unknown(self):
        sql = statements()
        self.assertIn("state='failed' AND error_code IS NOT NULL AND error_code='worker_failed'", sql)
        self.assertIn("state<>'failed' AND error_code IS NULL", sql)
        self.assertIn("CHECK (state NOT IN ('failed','succeeded') OR delivery_available)", sql)

    def test_sticky_cancellation_and_uncertain_lease_cannot_requeue_or_succeed(self):
        sql = statements()
        task = body("guard_owner_codex_task")
        self.assertIn("OLD.cancel_requested_at IS NOT NULL", task)
        self.assertIn("NEW.cancel_requested_at IS DISTINCT FROM OLD.cancel_requested_at", task)
        self.assertIn("NEW.state='succeeded' AND NEW.cancel_requested_at IS NOT NULL", task)
        self.assertIn("OLD.state='cancel_requested' AND NEW.state IN ('needs_reconciliation','failed','cancelled')", task)
        self.assertIn("OLD.state='needs_reconciliation' AND NEW.state IN ('succeeded','failed','cancelled')", task)
        self.assertIn("OLD.state='needs_reconciliation' AND NEW.state='needs_reconciliation'", task)
        self.assertNotIn("OLD.state='needs_reconciliation' AND NEW.state IN ('queued'", task)
        self.assertIn("CHECK (state<>'succeeded' OR cancel_requested_at IS NULL)", sql)
        self.assertIn("NEW.state='needs_reconciliation' AND NEW.code='cancel_requested'", body("guard_owner_codex_event"))

    def test_delivery_requires_both_bound_protected_receipt_hashes(self):
        sql = statements()
        self.assertIn("NOT delivery_available OR (state IN ('succeeded','failed','cancelled')", sql)
        self.assertIn("intent_receipt_sha256 IS NOT NULL AND outcome_receipt_sha256 IS NOT NULL", sql)
        self.assertIn("state='succeeded' AND result_sha256 IS NOT NULL AND delivery_available", sql)
        self.assertIn("OLD.intent_receipt_sha256 IS NOT NULL", body("guard_owner_codex_task"))
        self.assertIn("NEW.intent_receipt_sha256 IS DISTINCT FROM OLD.intent_receipt_sha256", body("guard_owner_codex_task"))
        # SQL validates shape/binding immutability only, not archive protection.
        # The service performs independent exact receipt verification first.
        source = SERVICE.read_text(encoding="utf-8")
        self.assertIn("self._receipt(intent, self.receipt_binding(row, \"intent\"))", source)
        self.assertIn("self._receipt(receipt, self.receipt_binding(row, \"outcome\"", source)
        self.assertIn('receipt.get("protected_archive_verified") is not True', source)

    def test_default_acl_inheritance_is_stripped_without_creating_grants(self):
        sql = statements()
        self.assertIn("REVOKE ALL ON owner_codex_sessions,owner_codex_tasks,owner_codex_task_events FROM PUBLIC", sql)
        self.assertIn("aclexplode(coalesce(c.relacl,acldefault('r',c.relowner)))", sql)
        self.assertIn("aclexplode(coalesce(p.proacl,acldefault('f',p.proowner)))", sql)
        self.assertIn("a.grantee<>c.relowner AND a.grantee<>0", sql)
        self.assertIn("a.grantee<>p.proowner AND a.grantee<>0", sql)
        self.assertIn("REVOKE ALL ON TABLE %I.%I FROM %I CASCADE", sql)
        self.assertIn("REVOKE ALL ON FUNCTION %I.%I() FROM %I CASCADE", sql)

    def test_service_requires_explicit_tenant_before_owner_binding(self):
        source = SERVICE.read_text(encoding="utf-8")
        self.assertIn("current_setting('app.tenant_id',true) AS configured_tenant", source)
        self.assertIn('scope["configured_tenant"] != owner.tenant_id', source)
        self.assertLess(source.index('scope["configured_tenant"] != owner.tenant_id'),
                        source.index("set_config('app.owner_object_id'"))

    def test_postgres_autocommit_is_rejected_before_any_authority_or_sql(self):
        source = SERVICE.read_text(encoding="utf-8")
        self.assertIn("raw.autocommit is not False", source)
        self.assertLess(source.index("raw.autocommit is not False"), source.index("authority = self.authorize(owner)"))


if __name__ == "__main__":
    unittest.main()
