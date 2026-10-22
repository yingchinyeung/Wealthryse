"""Medallion step S3: the layer migrations (068 gold, 069 bronze, 070 silver) on a real PostgreSQL.

Needs a disposable server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI
starts one). Skipped otherwise. Creates and drops its own databases and roles and
uses synthetic data only.

LayerViewTests applies every migration in order to an empty database, as a new
environment does, and checks each view left in public against the layer table
behind it. Until a layer migration exists there is nothing to check.

BatchRehearsalTests builds a database shaped like production before S3 (the
held-back migrations unapplied, the agent schema installed, the logins set up by
bootstrap_postgres.py) and runs each batch as production will: a snapshot with
scripts/medallion_layers.py, bootstrap_postgres.py --only-migration, then the check.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ADMIN_DSN = os.environ.get("AGENT_TEST_PG_ADMIN_DSN", "")
ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations" / "postgres"
UNDO = ROOT / "infra" / "medallion" / "undo"
TENANT = "00000000-0000-0000-0000-000000000001"
OTHER_TENANT = "00000000-0000-0000-0000-000000000002"
ACCOUNTS = {TENANT: "U9100001", OTHER_TENANT: "U9200001"}
OWNER = "medallion_test_owner"
APP, WEB, APPROVAL = "medallion_test_app", "medallion_test_web", "medallion_test_approval"
COLLECTOR = "ibkr_live_collector_trial"
PASSWORD = "medallion-test-only-password"  # throwaway roles in a disposable test server
TOLERATED_MIGRATIONS = {"027_legacy_external_market_data_approval.sql", "056_specialist_audit_ledger.sql"}
HELD_BACK = ("054_shadow_workflow_evidence.sql", "055_fx_evidence_versions.sql", "059_owner_codex_tasks.sql",
             "060_shadow_workflow_date_policies.sql", "061_shadow_workflow_leases.sql")
LAYER_FILES = sorted(path.name for path in MIGRATIONS.glob("*_medallion_*.sql"))
# Later migrations that create new layer objects and move them into their layer as 068 does,
# so undoing a layer moves them back to public and running them again moves them in again.
LAYER_CREATORS = ("073_certified_figures.sql", "082_kiwoom_bronze.sql")
# Later migrations that only run once 068 to 070 have created the layer schemas, so a build
# without the layer migrations skips these too.
NEEDS_LAYERS = ("074_silver_history.sql", "083_silver_write_watch.sql")
# Production's ledger has this version, applied outside the repository.
OUTSIDE = ("067_hong_kong_china_external_market_data_approval.sql",)
LAYERS = ("bronze", "silver", "gold")
RUNTIME_PATH = 'search_path="$user", bronze, silver, gold, public'
_MODULES: dict = {}


def setUpModule():
    # CI tests the installed package, which looks for migrations next to its install folder.
    from ibkr_etl import db

    patcher = patch.object(db, "POSTGRES_MIGRATIONS_DIR", MIGRATIONS)
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)


def _script(name: str):
    if name not in _MODULES:
        spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _MODULES[name] = module
    return _MODULES[name]


def _params(database: str, user: str | None = None, tenant: str | None = TENANT) -> dict:
    from psycopg.conninfo import conninfo_to_dict

    params = conninfo_to_dict(ADMIN_DSN)
    params["dbname"] = database
    if user:
        params["user"], params["password"] = user, PASSWORD
    if tenant:
        params["options"] = f"-c app.tenant_id={tenant}"
    return params


def _dsn(database: str, user: str | None = None, tenant: str | None = TENANT) -> str:
    from psycopg.conninfo import make_conninfo

    return make_conninfo(**_params(database, user, tenant))


def _url(database: str, user: str) -> str:
    from urllib.parse import quote

    params = _params(database, user, None)
    return (f"postgresql://{quote(user)}:{quote(PASSWORD)}@{params.get('host', 'localhost')}:"
            f"{params.get('port', '5432')}/{database}")


def _connect(database: str, user: str = OWNER, tenant: str | None = TENANT, **kwargs):
    import psycopg

    return psycopg.connect(_dsn(database, user, tenant), **kwargs)


def _catalog() -> dict[str, str]:
    """name -> layer for every catalog object that moves to a layer schema."""
    entries = json.loads((ROOT / "infra" / "medallion" / "catalog.json").read_text(encoding="utf-8"))["postgres"]
    return {key.split(".", 1)[1]: value["layer"] for key, value in entries.items()
            if key.startswith("public.") and value["layer"] in LAYERS}


def _made_in_layer() -> set[tuple[str, str]]:
    """(layer, name) for every catalog table a migration makes in its layer (074's history)."""
    entries = json.loads((ROOT / "infra" / "medallion" / "catalog.json").read_text(encoding="utf-8"))["postgres"]
    return {tuple(key.split(".", 1)) for key, value in entries.items() if key.split(".", 1)[0] in LAYERS}


def _build(database: str, *, skip=(), stop_before_layers=False, before_layers=None) -> list[str]:
    """Create the database and apply migrations in order as the owner, recording each like db.py."""
    import psycopg

    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (OWNER,)).fetchone() is None:
            # Not a superuser, so forced row-level security applies as on Azure.
            admin.execute(f"CREATE ROLE {OWNER} LOGIN CREATEROLE PASSWORD '{PASSWORD}'")
        admin.execute(f'CREATE DATABASE "{database}" OWNER {OWNER}')
    with psycopg.connect(_dsn(database), autocommit=True) as admin:
        admin.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    applied = []
    with _connect(database) as owner:
        owner.execute("CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, sha256 TEXT NOT NULL, "
                      "applied_at TEXT NOT NULL)")
        owner.commit()
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if LAYER_FILES and path.name == LAYER_FILES[0]:
                if stop_before_layers:
                    break
                if before_layers:
                    before_layers(owner)
            if path.name in skip:
                continue
            text = path.read_text(encoding="utf-8")
            try:
                owner.execute(text)
                owner.commit()
            except Exception:
                owner.rollback()
                if path.name not in TOLERATED_MIGRATIONS:
                    raise
            owner.execute("INSERT INTO schema_migrations VALUES (%s, %s, '2026-10-01T00:00:00+00:00')",
                          (path.name, hashlib.sha256(text.encode("utf-8")).hexdigest()))
            if owner.execute("SELECT to_regclass('public.tenants')").fetchone()[0] and \
                    owner.execute("SELECT 1 FROM tenants").fetchone() is None:
                for tenant in (OTHER_TENANT, TENANT):  # each written under its own tenant id
                    owner.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant,))
                    owner.execute("INSERT INTO tenants(tenant_id, name, status, created_at) "
                                  "VALUES (%s, 'Test', 'active', '2026-01-01')", (tenant,))
            owner.commit()
            applied.append(path.name)
    return applied


def _drop(database: str, roles=()) -> None:
    import psycopg

    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        for role in roles:
            admin.execute(f"DROP ROLE IF EXISTS {role}")


def _layout(connection) -> dict:
    """Where each catalog object is and everything that must survive a move."""
    found = {}
    for name in sorted(_catalog()):
        rows = connection.execute(
            "SELECT n.nspname, c.oid, c.relkind, c.relacl::text, c.relrowsecurity, c.relforcerowsecurity "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relname = %s AND n.nspname IN ('public', 'bronze', 'silver', 'gold') ORDER BY 1",
            (name,)).fetchall()
        if not rows:
            continue
        real = next((row for row in rows if row[0] != "public"), rows[0])
        oid = real[1]
        found[name] = {
            "schema": real[0], "kind": real[2], "acl": real[3], "rls": [real[4], real[5]],
            "compat_view": [row[0] for row in rows if row[0] == "public" and row[2] == "v" and real[0] != "public"],
            "columns": connection.execute(
                "SELECT attname, format_type(atttypid, atttypmod), attnotnull, attacl::text FROM pg_attribute "
                "WHERE attrelid = %s AND attnum > 0 AND NOT attisdropped ORDER BY attnum", (oid,)).fetchall(),
            "constraints": connection.execute(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = %s ORDER BY 1",
                (oid,)).fetchall(),
            "incoming": connection.execute(
                "SELECT conrelid::regclass::text, conname FROM pg_constraint WHERE confrelid = %s ORDER BY 1, 2",
                (oid,)).fetchall(),
            "indexes": connection.execute(
                "SELECT indexrelid::regclass::text FROM pg_index WHERE indrelid = %s ORDER BY 1", (oid,)).fetchall(),
            "policies": connection.execute(
                "SELECT polname, polcmd, pg_get_expr(polqual, polrelid), pg_get_expr(polwithcheck, polrelid) "
                "FROM pg_policy WHERE polrelid = %s ORDER BY 1", (oid,)).fetchall(),
            "triggers": connection.execute(
                "SELECT tgname FROM pg_trigger WHERE tgrelid = %s AND NOT tgisinternal ORDER BY 1", (oid,)).fetchall(),
        }
    return found


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class LayerViewTests(unittest.TestCase):
    """Every migration in order on an empty database: the check CI runs on each change."""

    @classmethod
    def setUpClass(cls):
        cls.database = "medallion_views_" + uuid.uuid4().hex[:8]
        cls.before_layers = None

        def capture(owner):
            cls.before_layers = _layout(owner)

        _build(cls.database, before_layers=capture)
        with _connect(cls.database) as owner:
            cls.after_layers = _layout(owner)
        if LAYER_FILES and any(path.name > LAYER_FILES[-1] for path in MIGRATIONS.glob("*.sql")):
            # Undoing the layers keeps what later migrations changed on layer tables (071 adds
            # lineage_run_id), so the expected result has every migration but the layer moves and
            # NEEDS_LAYERS, which the undo test takes out first.
            unlayered = cls.database + "_unlayered"
            try:
                _build(unlayered, skip=(*LAYER_FILES, *NEEDS_LAYERS))
                with _connect(unlayered) as owner:
                    cls.before_layers = _layout(owner)
            finally:
                _drop(unlayered)

    @classmethod
    def tearDownClass(cls):
        _drop(cls.database)

    def test_each_view_in_public_matches_the_layer_table_behind_it(self):
        with _connect(self.database) as owner:
            rows = owner.execute(
                "SELECT n.nspname, c.relname, c.oid, c.relacl::text, v.oid, v.relkind, v.reloptions, v.relacl::text "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "LEFT JOIN pg_class v ON v.relname = c.relname AND v.relnamespace = 'public'::regnamespace "
                "WHERE n.nspname IN ('bronze', 'silver', 'gold') AND c.relkind IN ('r', 'p', 'v') ORDER BY 1, 2"
            ).fetchall()
            # Tables a migration makes in their layer never were in public, so they have no view there.
            native = [row for row in rows if (row[0], row[1]) in _made_in_layer()]
            self.assertEqual([row[4] for row in native], [None] * len(native))
            rows = [row for row in rows if row not in native]
            self.assertEqual(len(rows), len([n for n, layer in _catalog().items()
                                              if layer in {r[0] for r in rows}]))
            columns = ("SELECT attname, format_type(atttypid, atttypmod), attacl::text FROM pg_attribute "
                       "WHERE attrelid = %s AND attnum > 0 AND NOT attisdropped ORDER BY attnum")
            for layer, name, table, table_acl, view, kind, options, view_acl in rows:
                with self.subTest(f"{layer}.{name}"):
                    self.assertEqual(kind, "v", f"public.{name} must be a view over {layer}.{name}")
                    self.assertIn("security_invoker=true", options or [])
                    # A view's columns are fixed when it is created: a later change to the table needs the
                    # view re-created, or code reading public.<name> misses the change.
                    self.assertEqual(owner.execute(columns, (view,)).fetchall(),
                                     owner.execute(columns, (table,)).fetchall())
                    self.assertEqual(self._without_owner(owner, view_acl, view),
                                     self._without_owner(owner, table_acl, table))
                    reads = owner.execute(
                        "SELECT DISTINCT d.refobjid FROM pg_rewrite r JOIN pg_depend d ON d.objid = r.oid "
                        "WHERE r.ev_class = %s AND d.refclassid = 'pg_class'::regclass AND d.refobjid <> %s",
                        (view, view)).fetchall()
                    self.assertEqual(reads, [(table,)])

    @staticmethod
    def _without_owner(connection, acl, oid):
        return connection.execute(
            "SELECT a.grantee::regrole::text, a.privilege_type, a.is_grantable FROM pg_class c, aclexplode(c.relacl) a "
            "WHERE c.oid = %s AND a.grantee <> c.relowner AND a.privilege_type IN ('SELECT', 'INSERT', 'UPDATE', "
            "'DELETE') ORDER BY 1, 2", (oid,)).fetchall() if acl else []

    def test_catalog_objects_are_in_their_layer_once_it_exists(self):
        with _connect(self.database) as owner:
            present = {row[0] for row in owner.execute(
                "SELECT nspname FROM pg_namespace WHERE nspname IN ('bronze', 'silver', 'gold')").fetchall()}
            for name, layer in sorted(_catalog().items()):
                if layer not in present:
                    continue
                with self.subTest(name):
                    self.assertIsNotNone(owner.execute("SELECT to_regclass(%s)", (f"{layer}.{name}",)).fetchone()[0])
                    self.assertEqual(owner.execute("SELECT relkind FROM pg_class WHERE oid = to_regclass(%s)",
                                                   (f"public.{name}",)).fetchone()[0], "v")

    def test_only_migration_tolerates_just_the_named_versions_without_a_file(self):
        from ibkr_etl.db import apply_postgres_migrations, connect

        last = sorted(path.name for path in MIGRATIONS.glob("*.sql"))[-1]
        connection = connect(Path("unused.db"), _url(self.database, OWNER), TENANT)
        self.addCleanup(connection.close)
        connection.execute("INSERT INTO schema_migrations VALUES (?, 'x', 'x')", OUTSIDE)
        with self.assertRaisesRegex(RuntimeError, r"not in the repository \['067_hong_kong_china_"):
            apply_postgres_migrations(connection, only=last)
        self.assertEqual(apply_postgres_migrations(connection, only=last, allow_applied_without_file=OUTSIDE), [])
        connection.execute("INSERT INTO schema_migrations VALUES ('999_gone.sql', 'x', 'x')")
        with self.assertRaisesRegex(RuntimeError, r"not in the repository \['999_gone\.sql'\]"):
            apply_postgres_migrations(connection, only=last, allow_applied_without_file=OUTSIDE)
        connection.rollback()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            _script("bootstrap_postgres").main(["--allow-applied-without-file", OUTSIDE[0]])

    @unittest.skipUnless(LAYER_FILES, "no layer migrations in this checkout")
    def test_undo_moves_each_layer_back_and_the_batches_apply_again(self):
        undo = _script("medallion_layers").undo_sql
        with _connect(self.database) as owner:
            # 074 and 083 add their own objects to silver, so as in production their undo runs before the
            # layers', the latest first.
            for name in reversed(NEEDS_LAYERS):
                owner.execute((UNDO / name.replace(".sql", "_undo.sql")).read_text(encoding="utf-8"))
                owner.commit()
            for layer in ("silver", "bronze", "gold"):  # the reverse of the batch order
                owner.execute(undo(layer))
                owner.commit()
            self.assertEqual(owner.execute("SELECT count(*) FROM pg_namespace WHERE nspname IN "
                                           "('bronze', 'silver', 'gold')").fetchone()[0], 0)
            self.assertEqual(_layout(owner), self.before_layers)
            for layer in ("silver", "bronze", "gold"):
                owner.execute(undo(layer))  # nothing left to undo
                owner.commit()
            for name in sorted([*LAYER_FILES, *LAYER_CREATORS, *NEEDS_LAYERS]):  # in migration order
                owner.execute((MIGRATIONS / name).read_text(encoding="utf-8"))
                owner.commit()
            self.assertEqual(_layout(owner), self.after_layers)


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class MirrorPrivilegesTests(unittest.TestCase):
    """mirror_privileges on a table moved by hand, so it runs before any layer migration exists."""

    def setUp(self):
        import psycopg

        self.database = "medallion_mirror_" + uuid.uuid4().hex[:8]
        self.role = "medallion_test_reader"
        _drop(self.database, (self.role,))
        _build(self.database, skip=[path.name for path in MIGRATIONS.glob("*.sql")])  # an empty database
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(f"CREATE ROLE {self.role} LOGIN PASSWORD '{PASSWORD}'")
        self.addCleanup(_drop, self.database, (self.role,))

    def test_column_grants_are_mirrored_and_keep_the_schema_usable(self):
        import psycopg

        from ibkr_etl.medallion_layers import mirror_privileges, mismatches

        with _connect(self.database, tenant=None) as owner:
            owner.execute("CREATE TABLE public.probe (tenant_id uuid, received_at text, content text)")
            owner.execute("INSERT INTO public.probe VALUES (gen_random_uuid(), '2026-10-01', 'secret')")
            owner.execute("CREATE SCHEMA gold")
            owner.execute("ALTER TABLE public.probe SET SCHEMA gold")
            owner.execute("CREATE VIEW public.probe WITH (security_invoker = true) AS SELECT * FROM gold.probe")
            # What bootstrap_postgres.py grants on public; a stale table-wide grant on the layer table.
            owner.execute(f"GRANT SELECT (tenant_id, received_at) ON public.probe TO {self.role}")
            owner.execute(f"GRANT SELECT ON gold.probe TO {self.role}")
            self.assertEqual(len(mismatches(owner, [self.role])), 2)
            mirror_privileges(owner, [self.role])
            self.assertEqual(mismatches(owner, [self.role]), [])
            self.assertTrue(owner.execute("SELECT has_schema_privilege(%s, 'gold', 'USAGE')",
                                          (self.role,)).fetchone()[0])
            owner.commit()
        with _connect(self.database, self.role, tenant=None) as reader:
            self.assertEqual(reader.execute("SELECT received_at FROM gold.probe").fetchall(), [("2026-10-01",)])
            reader.execute("SET search_path = gold, public")  # what bootstrap gives runtime logins
            self.assertEqual(reader.execute("SELECT received_at FROM probe").fetchall(), [("2026-10-01",)])
            reader.rollback()
            # PostgreSQL checks every column of a security_invoker view on the table behind
            # it, so column grants alone never read through the view in public.
            for statement in ("SELECT content FROM gold.probe", "SELECT received_at FROM public.probe"):
                with self.subTest(statement), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    reader.execute(statement)
                reader.rollback()


def _seed(owner) -> None:
    """A few synthetic rows per tenant in the tables the batches move, written as the owner."""
    now = datetime.now(timezone.utc)
    old, recent, ancient = now - timedelta(days=40), now - timedelta(hours=1), now - timedelta(days=400)
    for tenant, account in ACCOUNTS.items():
        owner.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant,))
        run = owner.execute(
            "INSERT INTO ingestion_runs (run_type, started_at, completed_at, status, requested_to) VALUES "
            "('staging-snapshot-import', '2026-10-01', '2026-10-01', 'success', '2026-09-30') RETURNING run_id"
        ).fetchone()[0]
        owner.execute("INSERT INTO accounts (account_id, first_seen_at, last_seen_at, source_json) "
                      "VALUES (%s, '2026-01-01', '2026-10-01', '{}')", (account,))
        owner.execute("INSERT INTO platform_subjects (subject_id, subject_type, display_name, base_currency, "
                      "lifecycle_status) VALUES (%s, 'physical_account', 'Synthetic', 'USD', 'active')", (account,))
        owner.execute("INSERT INTO raw_reports (run_id, received_at, content_sha256, byte_count, file_path) "
                      "VALUES (%s, '2026-10-01', %s, 10, 'synthetic/report.xml')",
                      (run, hashlib.sha256(account.encode()).hexdigest()))
        owner.execute("INSERT INTO flex_rows (account_id, section, record_type, source_key, row_sha256, data_json, "
                      "first_seen_run_id, last_seen_run_id) SELECT %s, 'Trades', 'Trade', 'key-' || g, md5(g::text), "
                      "'{}', %s, %s FROM generate_series(1, 5) g", (account, run, run))
        owner.execute("INSERT INTO nav_snapshots (account_id, snapshot_date, currency, total_text, row_sha256, "
                      "last_seen_run_id) SELECT %s, to_char(date '2026-09-01' + g, 'YYYY-MM-DD'), 'USD', '1000.00', "
                      "md5(g::text), %s FROM generate_series(0, 9) g", (account, run))
        owner.execute("INSERT INTO trades (account_id, source_key, row_sha256, last_seen_run_id) "
                      "VALUES (%s, 'trade-1', 'x', %s)", (account, run))
        owner.execute(
            "INSERT INTO reporting_snapshots (snapshot_id, subject_id, period_start, period_end, as_of_at, "
            "reporting_currency, formula_version, source_manifest_json, data_sha256, quality_status, status, "
            "created_at, snapshot_payload_json) VALUES (gen_random_uuid(), %s, '2026-09-01', '2026-09-30', "
            "'2026-09-30', 'USD', 'v1', '{}', %s, 'pass', 'frozen', '2026-10-01', '{\"synthetic\": true}')",
            (account, "a" * 64))
        for observed in (old, recent):
            owner.execute("INSERT INTO live_account_values (account_id, observed_at, base_currency, "
                          "net_liquidation) VALUES (%s, %s, 'USD', 1000)", (account, observed))
            owner.execute("INSERT INTO live_positions (account_id, observed_at, conid, symbol, sec_type, currency, "
                          "quantity) VALUES (%s, %s, '1001', 'SYN', 'STK', 'USD', 10)", (account, observed))
            owner.execute("INSERT INTO live_quotes (conid, observed_at, symbol, currency, market_data_type) "
                          "VALUES ('1001', %s, 'SYN', 'USD', 1)", (observed,))
        for exec_id, executed in (("old", ancient), ("new", recent)):
            owner.execute("INSERT INTO live_executions (exec_id, account_id, executed_at, conid, symbol, sec_type, "
                          "currency, side, quantity, price, first_seen_at, updated_at) VALUES (%s, %s, %s, '1001', "
                          "'SYN', 'STK', 'USD', 'BUY', 1, 10, %s, %s)", (exec_id, account, executed, executed, executed))
        owner.execute("INSERT INTO live_contracts (conid, symbol, sec_type, currency, refreshed_at) "
                      "VALUES ('1001', 'SYN', 'STK', 'USD', %s)", (now,))
        owner.execute("INSERT INTO live_daily_bars (conid, what_to_show, bar_date, close_price, currency, fetched_at) "
                      "VALUES ('1001', 'TRADES', '2026-10-01', 10, 'USD', %s)", (now,))
        template = hashlib.sha256(f"template {account}".encode()).hexdigest()
        owner.execute("INSERT INTO factsheet_design_files (sha256, content_type, byte_count, content, created_at) "
                      "VALUES (%s, 'application/pdf', 4, '\\x25504446', '2026-10-01')", (template,))
        owner.execute("INSERT INTO factsheet_design_drafts (draft_id, subject_id, title, template_sha256, "
                      "more_examples_json, status, current_version, build_json, created_by, created_at, updated_at) "
                      "VALUES (gen_random_uuid(), %s, 'Synthetic', %s, '[]', 'review', 1, '{}', 'test', "
                      "'2026-10-01', '2026-10-01')", (account, template))
    owner.execute("SELECT set_config('app.tenant_id', %s, false)", (TENANT,))
    owner.commit()


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
@unittest.skipUnless(len(LAYER_FILES) == len(LAYERS),
                     "all three layer migrations are needed; main gets one on each batch day")
class BatchRehearsalTests(unittest.TestCase):
    """The three batches as production runs them, then what each login can still do."""

    @classmethod
    def setUpClass(cls):
        import psycopg

        cls.database = "medallion_batches_" + uuid.uuid4().hex[:8]
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            for role in (APP, WEB, APPROVAL, COLLECTOR):
                admin.execute(f"DROP ROLE IF EXISTS {role}")
        applied = _build(cls.database, skip=HELD_BACK, stop_before_layers=True)
        with _connect(cls.database) as owner:
            owner.execute("INSERT INTO schema_migrations VALUES (%s, 'x', '2026-10-01T00:00:00+00:00')", OUTSIDE)
            _seed(owner)
            for text in _script("install_agent_schema").agent_schema_sql(owner):
                owner.execute(text)
            owner.commit()
        files = sorted(path.name for path in MIGRATIONS.glob("*.sql"))
        # The logins as an earlier bootstrap left them; it applies nothing.
        cls._bootstrap("--only-migration", applied[-1], "--allow-pending",
                       ",".join(name for name in files if name not in applied))
        with _connect(cls.database) as owner:
            cls.rights_before = cls._rights(owner)
        checks = _script("medallion_layers")
        cls.batches = {}
        for batch in checks.LAYERS:
            name = checks.BATCH_MIGRATION[batch]
            with _connect(cls.database) as owner:
                done = {row[0] for row in owner.execute("SELECT version FROM schema_migrations").fetchall()}
            before = cls._state()
            output = cls._bootstrap("--only-migration", name, "--allow-pending",
                                    ",".join(f for f in files if f not in done and f != name))
            after = cls._state()
            problems, notes = checks.compare(before, after, batch)
            problems += checks.after_problems(after, [layer for layer in checks.LAYERS if layer in after["layers"]])
            cls.batches[batch] = {"before": before, "after": after, "problems": problems, "notes": notes,
                                  "output": output}

    @classmethod
    def tearDownClass(cls):
        _drop(cls.database, (APP, WEB, APPROVAL, COLLECTOR, "ibkr_specialist_observation_trial",
                             "ibkr_specialist_observation_reader"))

    @classmethod
    def _bootstrap(cls, *args) -> str:
        environment = {
            "DATABASE_ADMIN_URL": _url(cls.database, OWNER), "PORTFOLIO_TENANT_ID": TENANT,
            "DATABASE_APP_ROLE": APP, "DATABASE_APP_PASSWORD": PASSWORD,
            "DATABASE_WEB_ROLE": WEB, "DATABASE_WEB_PASSWORD": PASSWORD,
            "DATABASE_APPROVAL_ROLE": APPROVAL, "DATABASE_APPROVAL_PASSWORD": PASSWORD,
        }
        if "--only-migration" in args:  # as production runs it
            args += ("--allow-applied-without-file", *OUTSIDE)
        output = io.StringIO()
        with patch.dict(os.environ, environment), contextlib.redirect_stdout(output):
            _script("bootstrap_postgres").main(list(args))
        return output.getvalue()

    @classmethod
    def _state(cls) -> dict:
        from psycopg.conninfo import make_conninfo

        conninfo = make_conninfo(_dsn(cls.database, OWNER, None), options="-c default_transaction_read_only=on")
        state = _script("medallion_layers").snapshot(conninfo, tenant=TENANT, account=ACCOUNTS[TENANT])
        return json.loads(json.dumps(state, default=str))  # as the --out file holds it

    @staticmethod
    def _rights(connection) -> dict:
        """Each login's rights on each moved object, by the name the code uses and on the table itself."""
        found = {}
        for name, layer in sorted(_catalog().items()):
            for schema in ("public", layer):
                if connection.execute("SELECT to_regclass(%s)", (f"{schema}.{name}",)).fetchone()[0] is None:
                    continue
                for role in (APP, WEB, APPROVAL):
                    found[(role, schema, name)] = connection.execute(
                        "SELECT p FROM unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE']) p "
                        "WHERE has_table_privilege(%s, %s::regclass, p) ORDER BY 1",
                        (role, f"{schema}.{name}")).fetchall()
        return found

    def _pending(self) -> list[str]:
        """The held-back files, and any migration added after the layer batches."""
        with _connect(self.database) as owner:
            done = {row[0] for row in owner.execute("SELECT version FROM schema_migrations").fetchall()}
        return [path.name for path in sorted(MIGRATIONS.glob("*.sql")) if path.name not in done]

    def _login(self, role, tenant=TENANT):
        connection = _connect(self.database, role, tenant)
        self.addCleanup(connection.close)
        return connection

    def test_each_batch_applies_only_its_migration_and_passes_the_check(self):
        checks = _script("medallion_layers")
        self.assertEqual(self.batches["gold"]["before"]["layers"], [])
        for batch, result in self.batches.items():
            with self.subTest(batch):
                self.assertEqual(result["problems"], [])
                self.assertIn(f"Migrations applied: {checks.BATCH_MIGRATION[batch]}.", result["output"])
                self.assertIn(batch, result["after"]["layers"])
        before = self.batches["gold"]["before"]
        # The checks compared real rows: data, frozen snapshots, the burn-in answers and the agent views.
        self.assertEqual(before["objects"]["flex_rows"]["rows"], 5)  # the tenant's own rows
        self.assertEqual(len(before["reporting_snapshots"]), 1)
        self.assertEqual(before["burn_in"]["latest_nav_date"], "2026-09-10")
        self.assertIsNotNone(before["burn_in"]["latest_import"])
        self.assertTrue([view for view, rows in before["agent"]["views"].items() if rows])
        self.assertFalse([view for view, rows in before["agent"]["views"].items() if isinstance(rows, str)])
        # And it notices a change it was not told about.
        problems, _ = checks.compare(before, self.batches["silver"]["after"], "gold")
        self.assertTrue([text for text in problems if text.startswith("schema_migrations gained")])

    def test_a_second_run_applies_nothing_and_logins_keep_the_same_rights(self):
        from ibkr_etl.medallion_layers import mismatches

        output = self._bootstrap("--only-migration", LAYER_FILES[-1], "--allow-pending", ",".join(self._pending()))
        self.assertIn("Migrations applied: none.", output)
        with _connect(self.database) as owner:
            self.assertEqual(mismatches(owner, [APP, WEB, APPROVAL]), [])
            after = self._rights(owner)
            settings = dict(owner.execute(
                "SELECT r.rolname, s.setconfig FROM pg_db_role_setting s JOIN pg_roles r ON r.oid = s.setrole "
                "WHERE r.rolname = ANY(%s)", ([APP, WEB, APPROVAL, OWNER],)).fetchall())
        for (role, schema, name), rights in self.rights_before.items():
            with self.subTest(f"{role} {name}"):
                self.assertEqual(after[(role, "public", name)], rights)
                self.assertEqual(after[(role, _catalog()[name], name)], rights)
        for role in (APP, WEB, APPROVAL):
            self.assertIn(RUNTIME_PATH, settings[role])
        self.assertNotIn(OWNER, settings)  # the migration login keeps public first

    def test_only_migration_refuses_and_changes_nothing(self):
        from ibkr_etl.db import apply_postgres_migrations, connect

        pending = self._pending()
        connection = connect(Path("unused.db"), _url(self.database, OWNER), TENANT)
        self.addCleanup(connection.close)
        ledger = connection.execute("SELECT version, sha256 FROM schema_migrations ORDER BY 1").fetchall()
        first = ledger[0][0]
        attempts = (
            ("999_missing.sql", pending, OUTSIDE, None),
            (LAYER_FILES[0], (), OUTSIDE, None),  # the held-back files are pending
            (LAYER_FILES[0], pending, (), None),  # the version applied outside the repository
            (LAYER_FILES[0], pending, OUTSIDE,
             ("UPDATE schema_migrations SET sha256 = 'drift' WHERE version = ?", (first,))),
            (LAYER_FILES[0], pending, OUTSIDE, ("INSERT INTO schema_migrations VALUES ('999_gone.sql', 'x', 'x')", ())),
        )
        for only, allow, without_file, change in attempts:
            with self.subTest(only=only, allow=bool(allow), without_file=without_file, change=change):
                if change:
                    connection.execute(*change)
                with self.assertRaises(RuntimeError):
                    apply_postgres_migrations(connection, only=only, allow_pending=allow,
                                              allow_applied_without_file=without_file)
                connection.rollback()
        self.assertEqual(apply_postgres_migrations(connection, only=LAYER_FILES[0], allow_pending=pending,
                                                   allow_applied_without_file=OUTSIDE), [])
        connection.rollback()
        self.assertEqual(connection.execute("SELECT version, sha256 FROM schema_migrations ORDER BY 1").fetchall(),
                         ledger)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self._bootstrap("--allow-pending", HELD_BACK[0])

    def test_migrations_use_public_whatever_the_login_search_path(self):
        from ibkr_etl.db import apply_postgres_migrations, connect

        pending = self._pending()
        connection = connect(Path("unused.db"), _url(self.database, OWNER), TENANT)
        self.addCleanup(connection.close)
        connection.execute("SET search_path = bronze, silver, gold, public")
        connection.commit()
        self.assertEqual(apply_postgres_migrations(connection, only=LAYER_FILES[0], allow_pending=pending,
                                                   allow_applied_without_file=OUTSIDE), [])
        self.assertIsNone(connection.execute("SELECT to_regclass('bronze.schema_migrations')").fetchone()[0])
        connection.commit()
        # Only for the migrations' own transaction.
        self.assertEqual(connection.execute("SELECT current_schema()").fetchone()[0], "bronze")

    def test_logins_read_and_write_as_before(self):
        import psycopg

        app = self._login(APP)
        self.assertEqual(app.execute("SELECT current_schema(), relnamespace::regnamespace::text FROM pg_class "
                                     "WHERE oid = to_regclass('flex_rows')").fetchone(), ("bronze", "bronze"))
        run = app.execute("SELECT max(run_id) FROM ingestion_runs").fetchone()[0]
        row = app.execute(
            "INSERT INTO flex_rows (account_id, section, record_type, source_key, row_sha256, data_json, "
            "first_seen_run_id, last_seen_run_id) VALUES (%s, 'Trades', 'Trade', 'key-new', 'x', '{}', %s, %s) "
            "ON CONFLICT (account_id, section, record_type, source_key) DO UPDATE SET last_seen_run_id = "
            "excluded.last_seen_run_id RETURNING tenant_id::text", (ACCOUNTS[TENANT], run, run)).fetchone()
        self.assertEqual(row[0], TENANT)
        app.rollback()

        web = self._login(WEB)
        self.assertEqual(web.execute("SELECT count(*) FROM nav_snapshots").fetchone()[0], 10)
        self.assertEqual(web.execute("SELECT count(*) FROM public.flex_rows").fetchone()[0], 5)
        with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
            web.execute("DELETE FROM nav_snapshots")
        web.rollback()

        approval = self._login(APPROVAL)
        self.assertEqual(approval.execute("SELECT count(*) FROM reporting_snapshots").fetchone()[0], 1)
        approval.execute("INSERT INTO factsheet_design_files (sha256, content_type, byte_count, content, created_at) "
                         "VALUES (%s, 'application/pdf', 4, '\\x25504446', '2026-10-02') "
                         "ON CONFLICT (tenant_id, sha256) DO NOTHING", ("b" * 64,))
        approval.execute("INSERT INTO public.factsheet_design_files (sha256, content_type, byte_count, content, "
                         "created_at) VALUES (%s, 'application/pdf', 4, '\\x25504446', '2026-10-02') "
                         "ON CONFLICT (tenant_id, sha256) DO NOTHING", ("b" * 64,))
        self.assertEqual(approval.execute("SELECT count(*) FROM factsheet_design_files").fetchone()[0], 2)
        approval.rollback()
        for statement in ("SELECT data_json FROM flex_rows", "SELECT file_path FROM public.raw_reports",
                          "UPDATE factsheet_design_files SET created_at = 'x'", "DELETE FROM nav_snapshots"):
            with self.subTest(statement), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                approval.execute(statement)
            approval.rollback()

    def test_writes_through_the_public_views(self):
        import psycopg

        app = self._login(APP)
        account = ACCOUNTS[TENANT]
        upsert = ("INSERT INTO public.nav_snapshots (account_id, snapshot_date, currency, total_text, row_sha256, "
                  "last_seen_run_id) SELECT %s, '2026-09-01', 'USD', %s, 'y', max(run_id) FROM public.ingestion_runs "
                  "ON CONFLICT (account_id, snapshot_date, currency, asset_class) DO UPDATE SET total_text = "
                  "excluded.total_text WHERE public.nav_snapshots.total_text IS DISTINCT FROM excluded.total_text "
                  "RETURNING total_text")
        self.assertEqual(app.execute(upsert, (account, "1500.00")).fetchall(), [("1500.00",)])
        self.assertEqual(app.execute(upsert, (account, "1500.00")).fetchall(), [])
        row = app.execute("INSERT INTO public.flex_rows (account_id, section, record_type, source_key, row_sha256, "
                          "data_json, first_seen_run_id, last_seen_run_id) SELECT %s, 'Trades', 'Trade', 'key-view', "
                          "'x', '{}', max(run_id), max(run_id) FROM ingestion_runs RETURNING flex_row_id, tenant_id::text",
                          (account,)).fetchone()
        self.assertGreater(row[0], 0)
        self.assertEqual(row[1], TENANT)
        self.assertEqual(app.execute(
            "UPDATE public.nav_snapshots n SET total_text = '900.00' FROM public.accounts a "
            "WHERE a.account_id = n.account_id AND n.snapshot_date = '2026-09-02'").rowcount, 1)
        self.assertEqual(app.execute(
            "DELETE FROM public.nav_snapshots n USING public.accounts a "
            "WHERE a.account_id = n.account_id AND n.snapshot_date = '2026-09-03'").rowcount, 1)
        self.assertEqual(len(app.execute("SELECT 1 FROM public.nav_snapshots FOR UPDATE").fetchall()), 9)
        app.execute("LOCK TABLE public.nav_snapshots IN SHARE ROW EXCLUSIVE MODE")
        app.rollback()
        # Through the view these fail, as they would on any view; nothing in the code uses them.
        failing = ("INSERT INTO public.nav_snapshots (account_id, snapshot_date, currency, row_sha256, "
                   "last_seen_run_id) VALUES ('x', 'x', 'USD', 'x', 1) ON CONFLICT ON CONSTRAINT nav_snapshots_pkey "
                   "DO NOTHING", "TRUNCATE public.trades")
        for statement in failing:
            with self.subTest(statement), self.assertRaises(psycopg.Error):
                app.execute(statement)
            app.rollback()
        with self.assertRaises(psycopg.Error):
            with app.cursor().copy("COPY public.trades (account_id) FROM STDIN"):
                pass
        app.rollback()
        # The runtime logins name the table itself first, where ON CONSTRAINT still works.
        app.execute("INSERT INTO nav_snapshots (account_id, snapshot_date, currency, row_sha256, last_seen_run_id) "
                    "SELECT %s, '2026-09-01', 'USD', 'x', max(run_id) FROM ingestion_runs "
                    "ON CONFLICT ON CONSTRAINT nav_snapshots_pkey DO NOTHING", (account,))
        app.rollback()

    def test_the_freshness_check_reads_only_its_columns(self):
        import psycopg

        from ibkr_etl.medallion_layers import mismatches

        bootstrap = _script("bootstrap_postgres")
        approval = self._login(APPROVAL)
        layers = _catalog()
        for table, columns in bootstrap.FRESHNESS_COLUMNS:
            listed = ", ".join(columns)
            # The check names tables unqualified; the login's search_path finds the layer table.
            self.assertTrue(approval.execute("SELECT %s::regclass = %s::regclass",
                                             (table, f"{layers[table]}.{table}")).fetchone()[0])
            for name in (f"{layers[table]}.{table}", table):
                with self.subTest(name):
                    approval.execute(f"SELECT {listed} FROM {name}").fetchall()
            # A security_invoker view needs every one of its columns on the table behind it.
            with self.subTest(f"public.{table}"), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                approval.execute(f"SELECT {listed} FROM public.{table}")
            approval.rollback()
        self.assertEqual(approval.execute("SELECT count(*) FROM flex_rows").fetchone()[0], 5)
        for name in ("public.flex_rows", "bronze.flex_rows", "flex_rows"):
            with self.subTest(name), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                approval.execute(f"SELECT data_json FROM {name}")
            approval.rollback()
        with _connect(self.database) as owner:
            self.assertEqual(mismatches(owner, [APP, WEB, APPROVAL]), [])

    def test_frozen_reporting_snapshots_stay_frozen_through_the_view(self):
        import psycopg

        app = self._login(APP)
        for table in ("public.reporting_snapshots", "reporting_snapshots"):
            with self.subTest(table), self.assertRaisesRegex(psycopg.errors.RaiseException, "immutable"):
                app.execute(f"UPDATE {table} SET data_sha256 = %s WHERE status = 'frozen'", ("b" * 64,))
            app.rollback()

    def test_row_level_security_still_isolates_tenants_through_the_views(self):
        import psycopg

        other = self._login(APP, OTHER_TENANT)
        for table, column in (("accounts", "account_id"), ("nav_snapshots", "account_id"),
                              ("flex_rows", "account_id"), ("reporting_snapshots", "subject_id"),
                              ("live_account_values", "account_id"), ("live_executions", "account_id")):
            with self.subTest(table):
                seen = other.execute(f"SELECT DISTINCT {column} FROM public.{table}").fetchall()
                self.assertEqual(seen, [(ACCOUNTS[OTHER_TENANT],)])
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            other.execute("INSERT INTO public.accounts (tenant_id, account_id, first_seen_at, last_seen_at, "
                          "source_json) VALUES (%s, 'U9100009', 'x', 'x', '{}')", (TENANT,))
        other.rollback()
        stranger = self._login(APP, str(uuid.uuid4()))
        self.assertEqual(stranger.execute("SELECT count(*) FROM public.nav_snapshots").fetchone()[0], 0)

    def test_bronze_tables_only_take_the_changes_the_code_makes(self):
        import psycopg

        app = self._login(APP)
        # Uncommitted: each statement below runs in a savepoint of this transaction and is undone.
        later = app.execute("INSERT INTO ingestion_runs (run_type, started_at, status) VALUES ('flex', 'x', "
                            "'success') RETURNING run_id").fetchone()[0]

        def attempt(statement, parameters=()):
            with app.transaction(force_rollback=True):
                return app.execute(statement, parameters).rowcount

        allowed = (
            ("UPDATE flex_rows SET row_sha256 = 'z', data_json = '{\"a\": 1}', last_seen_run_id = %s "
             "WHERE source_key = 'key-1'", (later,), 1),
            ("DELETE FROM live_account_values WHERE observed_at < now() - interval '30 days'", (), 1),
            ("DELETE FROM live_quotes WHERE observed_at < now() - interval '30 days'", (), 1),
            ("DELETE FROM live_executions WHERE executed_at < now() - interval '366 days'", (), 1),
            ("UPDATE live_executions SET commission = 1, commission_currency = 'USD', updated_at = now()", (), 2),
            ("UPDATE live_contracts SET long_name = 'Synthetic Inc', refreshed_at = now()", (), 1),
            ("UPDATE live_daily_bars SET close_price = 11, fetched_at = now()", (), 1),
        )
        for statement, parameters, count in allowed:
            with self.subTest(statement):
                self.assertEqual(attempt(statement, parameters), count)
        refused = (
            ("UPDATE flex_rows SET first_seen_run_id = %s WHERE source_key = 'key-1'", (later,)),
            ("UPDATE flex_rows SET source_key = 'other' WHERE source_key = 'key-1'", ()),
            ("DELETE FROM flex_rows", ()),
            ("UPDATE raw_reports SET file_path = 'elsewhere'", ()),
            ("DELETE FROM raw_reports", ()),
            ("DELETE FROM live_account_values WHERE observed_at > now() - interval '1 day'", ()),
            ("DELETE FROM live_quotes WHERE observed_at > now() - interval '1 day'", ()),
            ("DELETE FROM live_executions WHERE executed_at > now() - interval '1 day'", ()),
            ("UPDATE live_executions SET price = 11", ()),
            ("UPDATE live_contracts SET conid = '1002'", ()),
            ("UPDATE live_daily_bars SET bar_date = '2026-10-02'", ()),
            ("UPDATE live_account_values SET net_liquidation = 1", ()),
        )
        for statement, parameters in refused:
            with self.subTest(statement), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                attempt(statement, parameters)
        self.assertEqual(app.execute("SELECT count(*) FROM live_positions").fetchone()[0], 2)
        app.rollback()

        with _connect(self.database) as owner:  # even the owner, short of disabling the trigger
            for statement in ("TRUNCATE bronze.flex_rows", "TRUNCATE bronze.live_quotes",
                              "UPDATE bronze.factsheet_design_files SET created_at = 'x'",
                              "DELETE FROM bronze.factsheet_design_files"):
                with self.subTest(statement), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    owner.execute(statement)
                owner.rollback()
            missing = owner.execute(
                "SELECT c.relname FROM pg_class c WHERE c.relnamespace = 'bronze'::regnamespace AND c.relkind = 'r' "
                "AND (SELECT count(*) FROM pg_trigger t WHERE t.tgrelid = c.oid AND t.tgname IN "
                "('bronze_append_only', 'bronze_no_truncate')) <> 2").fetchall()
            self.assertEqual(missing, [])

    def test_factsheet_drafts_still_reference_their_uploaded_files(self):
        import psycopg

        with _connect(self.database) as owner:
            references = owner.execute(
                "SELECT confrelid::regclass::text FROM pg_constraint WHERE contype = 'f' "
                "AND conrelid = 'public.factsheet_design_drafts'::regclass AND confrelid = "
                "'bronze.factsheet_design_files'::regclass").fetchall()
            self.assertEqual(references, [("bronze.factsheet_design_files",)] * 2)
            draft = ("INSERT INTO factsheet_design_drafts (draft_id, subject_id, title, template_sha256, "
                     "example_sha256, more_examples_json, status, current_version, build_json, created_by, created_at, "
                     "updated_at) VALUES (gen_random_uuid(), %s, 'Synthetic', %s, %s, '[]', 'review', 1, '{}', "
                     "'test', 'x', 'x')")
            template = hashlib.sha256(f"template {ACCOUNTS[TENANT]}".encode()).hexdigest()
            owner.execute(draft, (ACCOUNTS[TENANT], template, template))
            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                owner.execute(draft, (ACCOUNTS[TENANT], template, "c" * 64))
            owner.rollback()

    def test_the_agent_views_return_rows_and_the_agent_schema_reinstalls(self):
        before = self.batches["gold"]["before"]["agent"]
        with _connect(self.database) as owner:
            for text in _script("install_agent_schema").agent_schema_sql(owner):
                owner.execute(text)
            owner.commit()
            moved = owner.execute(
                "SELECT count(*) FROM pg_proc p JOIN pg_type t ON t.oid = p.prorettype JOIN pg_namespace n "
                "ON n.oid = t.typnamespace WHERE p.pronamespace = 'agent'::regnamespace "
                "AND n.nspname IN ('bronze', 'silver', 'gold')").fetchone()[0]
        self.assertEqual(self._state()["agent"], before)
        self.assertGreater(moved, 0)  # reinstalled functions return the layer tables' row types

    def test_the_specialist_observation_reader_still_verifies(self):
        from psycopg.rows import dict_row

        from ibkr_etl.specialist_observation_access import (
            TRIAL_ROLE, grant_statements, layer_grant_statements, verify_permissions,
        )
        from ibkr_etl.specialist_observation_runtime import NAV_VIEW_SHA256

        with _connect(self.database, row_factory=dict_row) as owner:
            owner.execute(f"CREATE ROLE {TRIAL_ROLE} NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE "
                          "NOREPLICATION NOBYPASSRLS")
            for statement in grant_statements(TRIAL_ROLE) + layer_grant_statements(owner, TRIAL_ROLE):
                owner.execute(statement)
            result = verify_permissions(owner, TRIAL_ROLE)
            owner.rollback()
        self.assertEqual(result["nav_view_sha256"], NAV_VIEW_SHA256)

    def test_the_specialist_observation_reader_reads_the_layer_tables(self):
        """Its column grants are refused through a view in public, so its session finds the layer tables first."""
        import psycopg
        from psycopg.rows import dict_row

        from ibkr_etl import specialist_observation_runtime as runtime
        from ibkr_etl.specialist_observation_access import COLUMNS, ROLE, grant_statements, layer_grant_statements

        with _connect(self.database, row_factory=dict_row, autocommit=True) as owner:
            owner.execute(f"CREATE ROLE {ROLE} NOLOGIN NOINHERIT")
            for statement in grant_statements(ROLE) + layer_grant_statements(owner, ROLE):
                owner.execute(statement)
            owner.execute(f"GRANT {ROLE} TO CURRENT_USER WITH INHERIT FALSE, SET TRUE")
        worker = _script("run_specialist_observation_worker")
        # The owner stands in for the observation login, which only ever sets the reader role.
        with _connect(self.database, tenant=None, row_factory=dict_row, autocommit=True) as c, \
                patch.object(runtime, "LOGIN", OWNER):
            with c.transaction():
                runtime.bind_observation_session(c)
                self.assertEqual(c.execute("SELECT current_user::text AS role").fetchone()["role"], ROLE)
                self.assertEqual(worker.check_relations(c), len(COLUMNS))
                with self.assertRaises(psycopg.errors.InsufficientPrivilege), c.transaction():
                    c.execute("SELECT tenant_id, account_id FROM public.accounts LIMIT 0")

    def test_the_live_collector_login_is_provisioned_on_the_bronze_tables(self):
        from ibkr_etl.live_collector_access import least_privilege_problems, privilege_report, provision

        with _connect(self.database, autocommit=True) as owner:
            provision(owner, password=PASSWORD + "-collector", database=self.database, role=COLLECTOR)
            self.assertEqual(least_privilege_problems(privilege_report(owner, COLLECTOR)), [])
        import psycopg

        params = _params(self.database, COLLECTOR)
        params["password"] = PASSWORD + "-collector"
        with psycopg.connect(**params) as collector:
            collector.execute("INSERT INTO live_quotes (conid, observed_at, symbol, currency, market_data_type) "
                              "VALUES ('1001', now(), 'SYN', 'USD', 1) ON CONFLICT (tenant_id, conid, observed_at) "
                              "DO NOTHING")
            collector.rollback()

    def test_held_back_migration_055_cannot_follow_the_silver_batch(self):
        import psycopg

        with _connect(self.database) as owner:
            owner.execute("SELECT 1")  # each file below runs in a savepoint of this transaction
            for name in HELD_BACK:
                text = (MIGRATIONS / name).read_text(encoding="utf-8")
                with self.subTest(name):
                    if name.startswith("055_"):
                        # It references accounts, now a view in public.
                        with self.assertRaises(psycopg.errors.WrongObjectType):
                            with owner.transaction():
                                owner.execute(text)
                    else:
                        with owner.transaction():
                            owner.execute(text)
            owner.rollback()


if __name__ == "__main__":
    unittest.main()
