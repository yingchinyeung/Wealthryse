"""Medallion step 3, the silver write lock (migration 083 and bootstrap_postgres.py) on a real PostgreSQL.

Needs a disposable server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI starts one).
Skipped otherwise. Builds its own database with every migration applied by a login that is not
a superuser, sets the logins up with bootstrap as production does, and uses no rows at all: a
statement that matches nothing still needs the write right and still fires a statement trigger.

The watch (083) notes writes to silver by any login outside the silver_loader group and never
refuses one. SILVER_WRITE_LOCK=on takes the app login's write rights on silver away; the loader
login keeps them, every login keeps reading, and bootstrap without the setting is the undo.
"""
from __future__ import annotations

import contextlib
import io
import os
import unittest
import uuid
from unittest.mock import patch

try:
    from test_medallion_layers_db import (ADMIN_DSN, APP, APPROVAL, LAYER_FILES, OWNER, PASSWORD, TENANT, WEB,
                                          _build, _connect, _drop, _script, _url)
except ImportError:  # run as tests.test_silver_write_lock_db
    from tests.test_medallion_layers_db import (ADMIN_DSN, APP, APPROVAL, LAYER_FILES, OWNER, PASSWORD, TENANT, WEB,
                                                _build, _connect, _drop, _script, _url)

LOADER = "medallion_test_loader"
GROUP = "silver_loader"
# A statement that matches no row: it needs the right and fires the statement trigger, and changes nothing.
SILVER_WRITES = (
    ("UPDATE nav_snapshots SET currency = currency WHERE false", "nav_snapshots", "UPDATE"),
    ("DELETE FROM silver.trades WHERE false", "trades", "DELETE"),
    ("INSERT INTO public.benchmark_prices SELECT * FROM silver.benchmark_prices WHERE false",
     "benchmark_prices", "INSERT"),
)
OTHER_WRITES = ("UPDATE flex_rows SET row_sha256 = row_sha256 WHERE false",  # bronze
                "DELETE FROM risk_analytics_snapshots WHERE false",  # gold
                "UPDATE ingestion_runs SET status = status WHERE false")  # public


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
@unittest.skipUnless(len(LAYER_FILES) == 3, "the silver lock needs the layer migrations")
class SilverWriteLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        cls.database = "silver_lock_" + uuid.uuid4().hex[:8]
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            for role in (APP, WEB, APPROVAL, LOADER):
                admin.execute(f"DROP ROLE IF EXISTS {role}")
        _build(cls.database)
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            # The group belongs to the whole server: another test's owner may have created it. On
            # Azure the admin login that runs 083 creates it and so may grant it.
            admin.execute(f"GRANT {GROUP} TO {OWNER} WITH ADMIN TRUE, INHERIT FALSE, SET FALSE")
        cls.first_output = cls._bootstrap()

    @classmethod
    def tearDownClass(cls):
        _drop(cls.database, (APP, WEB, APPROVAL, LOADER))

    @classmethod
    def _bootstrap(cls, lock: str | None = None, loader: bool = True) -> str:
        environment = {
            "DATABASE_ADMIN_URL": _url(cls.database, OWNER), "PORTFOLIO_TENANT_ID": TENANT,
            "DATABASE_APP_ROLE": APP, "DATABASE_APP_PASSWORD": PASSWORD,
            "DATABASE_WEB_ROLE": WEB, "DATABASE_WEB_PASSWORD": PASSWORD,
            "DATABASE_APPROVAL_ROLE": APPROVAL, "DATABASE_APPROVAL_PASSWORD": PASSWORD,
        }
        if loader:
            environment.update(DATABASE_LOADER_ROLE=LOADER, DATABASE_LOADER_PASSWORD=PASSWORD)
        if lock is not None:
            environment["SILVER_WRITE_LOCK"] = lock
        drop = [key for key in ("SILVER_WRITE_LOCK", "DATABASE_LOADER_PASSWORD") if key not in environment]
        output = io.StringIO()
        with patch.dict(os.environ, environment), contextlib.redirect_stdout(output):
            for key in drop:
                os.environ.pop(key, None)
            _script("bootstrap_postgres").main([])
        return output.getvalue()

    def _login(self, role):
        connection = _connect(self.database, role)
        self.addCleanup(connection.close)
        return connection

    def _write(self, role, statement) -> bool:
        """True when the login may run the statement; it is rolled back either way."""
        import psycopg

        connection = self._login(role)
        try:
            connection.execute(statement)
            return True
        except psycopg.errors.InsufficientPrivilege:
            return False
        finally:
            connection.rollback()

    def _watch(self) -> list[tuple]:
        with _connect(self.database) as owner:
            return owner.execute("SELECT login, relation, operation FROM public.silver_write_watch "
                                 "ORDER BY watch_id").fetchall()

    def _clear_watch(self):
        with _connect(self.database) as owner:
            owner.execute("DELETE FROM public.silver_write_watch")

    def test_every_silver_table_is_watched_but_the_history(self):
        with _connect(self.database) as owner:
            unwatched = owner.execute(
                "SELECT c.relname FROM pg_class c WHERE c.relnamespace = 'silver'::regnamespace "
                "AND c.relkind IN ('r', 'p') AND NOT EXISTS (SELECT 1 FROM pg_trigger t WHERE t.tgrelid = c.oid "
                "AND t.tgname = 'silver_write_watch') ORDER BY 1").fetchall()
        self.assertTrue(unwatched)
        self.assertEqual([name for (name,) in unwatched if not name.endswith("_history")], [])

    def test_the_watch_notes_only_logins_outside_the_loader_group_and_refuses_nothing(self):
        import psycopg

        self._clear_watch()
        for statement, _, _ in SILVER_WRITES:
            for role in (APP, LOADER, OWNER):
                with self.subTest(role=role, statement=statement):
                    connection = self._login(role)
                    connection.execute(statement)
                    connection.commit()
        self.assertEqual(self._watch(), [(APP, relation, operation) for _, relation, operation in SILVER_WRITES])
        # Nobody but the owner writes the watch, and a write that is rolled back leaves no note.
        app = self._login(APP)
        self.assertEqual(app.execute("SELECT count(*) FROM public.silver_write_watch").fetchone()[0], 3)
        for statement in ("INSERT INTO public.silver_write_watch (login, relation, operation) "
                          "VALUES ('x', 'x', 'INSERT')", "DELETE FROM public.silver_write_watch"):
            with self.subTest(statement), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                app.execute(statement)
            app.rollback()
        app.execute(SILVER_WRITES[0][0])
        app.rollback()
        self.assertEqual(len(self._watch()), 3)

    def test_the_lock_takes_silver_writes_from_the_app_login_only_and_bootstrap_without_it_gives_them_back(self):
        from ibkr_etl.medallion_layers import mismatches, silver_write_rights

        self.assertIn("app login still writes (SILVER_WRITE_LOCK off)", self.first_output)
        for statement, _, _ in SILVER_WRITES:
            self.assertTrue(self._write(APP, statement), statement)

        output = self._bootstrap("on")
        self.assertIn("app login locked out", output)
        for statement, _, _ in SILVER_WRITES:
            with self.subTest(statement):
                self.assertFalse(self._write(APP, statement))
                self.assertTrue(self._write(LOADER, statement))
        for statement in OTHER_WRITES:  # the app login keeps every other right
            with self.subTest(statement):
                self.assertTrue(self._write(APP, statement))
                self.assertTrue(self._write(LOADER, statement))
        for role in (APP, LOADER, WEB):  # and everyone still reads silver
            self.assertEqual(self._login(role).execute("SELECT count(*) FROM nav_snapshots").fetchone()[0], 0)
        with _connect(self.database) as owner:
            self.assertEqual(silver_write_rights(owner, [APP, WEB, APPROVAL]), [])
            self.assertEqual(mismatches(owner, [APP, WEB, APPROVAL, LOADER, GROUP]), [])
            # A silver table made later keeps the lock (default privileges).
            owner.execute("CREATE TABLE silver.lock_probe (id int)")
            owner.commit()
            rights = owner.execute("SELECT has_table_privilege(%s, 'silver.lock_probe', 'INSERT'), "
                                   "has_table_privilege(%s, 'silver.lock_probe', 'INSERT')", (APP, LOADER)).fetchone()
            self.assertEqual(tuple(rights), (False, True))
            owner.execute("DROP TABLE silver.lock_probe")
            owner.commit()

        # A second run with the lock on changes nothing.
        self.assertIn("app login locked out", self._bootstrap("on"))
        self.assertFalse(self._write(APP, SILVER_WRITES[0][0]))

        # The undo: bootstrap without the setting.
        self.assertIn("still writes", self._bootstrap())
        for statement, _, _ in SILVER_WRITES:
            with self.subTest(statement):
                self.assertTrue(self._write(APP, statement))
                self.assertTrue(self._write(LOADER, statement))

    def test_the_lock_needs_the_loader_login(self):
        with self.assertRaisesRegex(RuntimeError, "needs the loader login"):
            self._bootstrap("on", loader=False)
        with self.assertRaisesRegex(RuntimeError, "must be on or off"):
            self._bootstrap("yes")

    def test_the_loader_login_has_the_app_logins_search_path_and_reads_through_it(self):
        loader = self._login(LOADER)
        self.assertEqual(loader.execute("SELECT current_schema()").fetchone()[0], "bronze")
        self.assertEqual(loader.execute("SELECT count(*) FROM flex_rows").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
