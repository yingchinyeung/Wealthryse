"""Migration 082 (bronze.kiwoom_responses) against a real PostgreSQL.

Needs a disposable server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI starts one).
Skipped otherwise. Creates and drops its own database with every migration applied by a
login that is not a superuser, as on Azure, so forced row-level security applies.
Synthetic connections, accounts and answers only.

The capture tests run the collector's writer (ibkr_etl.kiwoom_bronze) against the table;
they are skipped on a branch that does not have that module yet.
"""
from __future__ import annotations

import hashlib
import json
import unittest
import unittest.mock
import uuid
from datetime import datetime, timedelta, timezone

try:
    from test_broker_onboarding_db import (ADMIN_DSN, OTHER_TENANT, OWNER_ROLE, TENANT, _dsn,
                                           _MigratedDatabase)
except ImportError:  # run as tests.test_kiwoom_bronze_db
    from tests.test_broker_onboarding_db import (ADMIN_DSN, OTHER_TENANT, OWNER_ROLE, TENANT, _dsn,
                                                 _MigratedDatabase)

try:
    from ibkr_etl import kiwoom_bronze
except ImportError:  # the writer arrives in its own change
    kiwoom_bronze = None

ACCOUNT = "KIWOOM-T0000001"
APP_KEY = "synthetic-app-key-0123456789"
APP_SECRET = "synthetic-app-secret-0123456789"
TOKEN = "synthetic-access-token-0123456789"
REQUESTED = datetime(2026, 10, 23, 7, 20, tzinfo=timezone.utc)


class _Database(_MigratedDatabase):
    def setUp(self):
        import psycopg

        from ibkr_etl.db import PostgresConnection, _hybrid_row_factory

        self.raw = psycopg.connect(_dsn(ADMIN_DSN, self.database, OWNER_ROLE),
                                   options=f"-c app.tenant_id={TENANT}", row_factory=_hybrid_row_factory)
        self.addCleanup(self.raw.close)
        self.db = PostgresConnection(self.raw)

    def _connect(self):
        import psycopg

        from ibkr_etl.db import PostgresConnection, _hybrid_row_factory

        raw = psycopg.connect(_dsn(ADMIN_DSN, self.database, OWNER_ROLE), options=f"-c app.tenant_id={TENANT}",
                              row_factory=_hybrid_row_factory)
        self.addCleanup(raw.close)
        return PostgresConnection(raw)

    def _broker_connection(self) -> str:
        connection_id = uuid.uuid4()
        self.db.execute(
            "INSERT INTO broker_connections (connection_id, broker, environment, display_name, key_vault_secret_name, "
            "credential_fingerprint, status) VALUES (?, 'kiwoom_rest', 'mock', ?, ?, ?, 'pending_check')",
            (connection_id, f"Kiwoom {connection_id.hex[:6]}", "broker-kiwoom-rest-" + connection_id.hex, "0" * 16))
        self.db.commit()
        return str(connection_id)

    def _run(self) -> int:
        run_id = self.db.execute(
            "INSERT INTO ingestion_runs (run_type, started_at, status) VALUES ('kiwoom_rest_bronze', ?, 'running') "
            "RETURNING run_id", (REQUESTED.isoformat(),)).fetchone()["run_id"]
        self.db.commit()
        return int(run_id)

    def _row(self, **overrides):
        body = overrides.pop("body", b'{"return_code":0,"return_msg":"ok"}')
        values = {
            "run_id": self.run_id, "tenant_id": TENANT, "connection_id": self.connection_id, "api_id": "ust21100",
            "request_key": json.dumps({"account": ACCOUNT, "fr_dt": "20261001"}), "page_no": 1, "cont_yn": "N",
            "next_key": None, "requested_at": REQUESTED, "received_at": REQUESTED + timedelta(seconds=1),
            "http_status": 200, "return_code": 0, "body": body, "body_sha256": hashlib.sha256(body).hexdigest(),
        }
        values.update(overrides)
        columns = ", ".join(values)
        marks = ", ".join("CAST(? AS JSONB)" if name == "request_key" else "?" for name in values)
        return self.db.execute(f"INSERT INTO bronze.kiwoom_responses ({columns}) VALUES ({marks})",
                               tuple(values.values()))

    def _refused(self, pattern: str, **overrides):
        with self.assertRaisesRegex(Exception, pattern):
            self._row(**overrides)
        self.db.rollback()


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class KiwoomResponsesTableTests(_Database):
    def setUp(self):
        super().setUp()
        self.connection_id = self._broker_connection()
        self.run_id = self._run()

    def test_migration_is_recorded_once(self):
        self.assertTrue(self.db.execute("SELECT to_regclass('bronze.kiwoom_responses') IS NOT NULL AS found")
                        .fetchone()["found"])

    def test_a_page_is_stored_with_its_sha256(self):
        self._row()
        self.db.commit()
        row = self.db.execute("SELECT body, body_sha256, request_key->>'account' AS account "
                              "FROM bronze.kiwoom_responses WHERE run_id = ?", (self.run_id,)).fetchone()
        self.assertEqual(bytes(row["body"]), b'{"return_code":0,"return_msg":"ok"}')
        self.assertEqual(row["account"], ACCOUNT)

    def test_it_is_append_only(self):
        self._row()
        self.db.commit()
        for statement in ("UPDATE bronze.kiwoom_responses SET page_no = 2 WHERE run_id = ?",
                          "DELETE FROM bronze.kiwoom_responses WHERE run_id = ?"):
            with self.subTest(statement=statement.split()[0]):
                with self.assertRaisesRegex(Exception, "append-only|refuse|not allowed|bronze"):
                    self.db.execute(statement, (self.run_id,))
                self.db.rollback()
        with self.assertRaisesRegex(Exception, "append-only|refuse|not allowed|bronze"):
            self.db.execute("TRUNCATE bronze.kiwoom_responses")
        self.db.rollback()

    def test_only_inquiry_ids_and_matching_digests_are_kept(self):
        self._refused("kiwoom_responses_api_id_check|check constraint", api_id="au10001")  # the token call
        self._refused("kiwoom_responses_api_id_check|check constraint", api_id="token")
        self._refused("kiwoom_responses_body_sha256", body_sha256="0" * 64)
        self._refused("received_after_requested", received_at=REQUESTED - timedelta(seconds=1))
        self._refused("check constraint", cont_yn="maybe")
        self._refused("check constraint", next_key="k" * 201)
        self._refused("check constraint", page_no=0)
        self._refused("check constraint", request_key=json.dumps(["not", "an", "object"]))

    def test_the_same_page_twice_keeps_the_first(self):
        self._row()
        inserted = self.db.execute(
            "INSERT INTO bronze.kiwoom_responses (run_id, tenant_id, connection_id, api_id, request_key, page_no, "
            "requested_at, received_at, http_status, body, body_sha256) "
            "SELECT run_id, tenant_id, connection_id, api_id, request_key, page_no, requested_at, received_at, "
            "http_status, body, body_sha256 FROM bronze.kiwoom_responses WHERE run_id = ? "
            "ON CONFLICT ON CONSTRAINT kiwoom_responses_page DO NOTHING", (self.run_id,))
        self.assertEqual(inserted.rowcount, 0)
        self._row(page_no=2)  # the next page of the same request is its own row
        self.db.commit()
        self.assertEqual(self.db.execute("SELECT count(*) AS n FROM bronze.kiwoom_responses WHERE run_id = ?",
                                         (self.run_id,)).fetchone()["n"], 2)

    def test_a_row_needs_a_known_connection_and_run(self):
        self._refused("kiwoom_responses_connection|foreign key", connection_id=str(uuid.uuid4()))
        self._refused("foreign key", run_id=self.run_id + 10_000)

    def test_tenants_are_isolated(self):
        self._refused("row-level security", tenant_id=OTHER_TENANT)
        self._row()
        self.db.commit()
        import psycopg

        with psycopg.connect(_dsn(ADMIN_DSN, self.database, OWNER_ROLE),
                             options=f"-c app.tenant_id={OTHER_TENANT}") as other:
            self.assertEqual(other.execute("SELECT count(*) FROM bronze.kiwoom_responses").fetchone()[0], 0)


class _Transport:
    """Kiwoom as the client sees it: a token, two pages of ust21100, then a refusal."""

    def __init__(self):
        self.calls = 0

    def __call__(self, url, headers, body):
        from ibkr_etl.kiwoom_client import RawAnswer

        self.calls += 1
        if url.endswith("/oauth2/token"):
            return 200, {}, {"return_code": 0, "token": TOKEN, "expires_dt": "20991231235959"}
        if url.endswith("/oauth2/revoke"):
            return 200, {}, {"return_code": 0}
        request = json.loads(body)
        if request.get("fr_dt") == "20200101":
            raw = '{"return_code":2,"return_msg":"no data"}'.encode()
            return 200, {"cont-yn": "N"}, RawAnswer(json.loads(raw), raw)
        page = 2 if headers.get("cont-yn") == "Y" else 1
        raw = json.dumps({"return_code": 0, "page": page, "rows": [{"amt": "1"}]}, separators=(" ,", ": ")).encode()
        continuation = {"cont-yn": "Y", "next-key": "NEXT-1"} if page == 1 else {"cont-yn": "N"}
        return 200, continuation, RawAnswer(json.loads(raw), raw)


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
@unittest.skipIf(kiwoom_bronze is None, "ibkr_etl.kiwoom_bronze is not on this branch yet")
class KiwoomBronzeCaptureTests(_Database):
    def setUp(self):
        super().setUp()
        self.connection_id = self._broker_connection()

    def _client(self):
        from ibkr_etl.kiwoom_client import KiwoomClient

        return KiwoomClient(environment="mock", app_key=APP_KEY, app_secret=APP_SECRET, transport=_Transport(),
                            sleep=lambda _: None, min_interval_seconds=0)

    def _target(self):
        class Target:
            connection_id = self.connection_id
            broker_account_id = ACCOUNT

        return Target()

    def test_every_page_and_a_refusal_are_stored_as_received(self):
        from ibkr_etl.kiwoom_client import KiwoomError

        bronze = kiwoom_bronze.KiwoomBronze(self._connect, TENANT)
        self.addCleanup(bronze.close)
        capture = bronze.for_target(self._target())
        client = self._client()
        client.set_capture(capture)
        pages = client.inquire("ust21100", {"fr_dt": "20261001", "to_dt": "20261022"})
        self.assertEqual(len(pages), 2)
        client.inquire("ust21100", {"fr_dt": "20261001", "to_dt": "20261022"})  # asked again: kept once
        with self.assertRaises(KiwoomError):
            client.inquire("ust21100", {"fr_dt": "20200101", "to_dt": "20200102"})
        client.revoke()
        capture.finish(True)

        rows = self.db.execute(
            "SELECT api_id, page_no, cont_yn, next_key, http_status, return_code, body, body_sha256, "
            "request_key FROM bronze.kiwoom_responses WHERE run_id = ? ORDER BY response_id",
            (capture.run_id,)).fetchall()
        self.assertEqual([(r["page_no"], r["cont_yn"], r["next_key"], r["return_code"]) for r in rows],
                         [(1, "Y", "NEXT-1", 0), (2, "N", None, 0), (1, "N", None, 2)])
        for row in rows:
            self.assertEqual(hashlib.sha256(bytes(row["body"])).hexdigest(), row["body_sha256"])
        self.assertIn(b'"return_code": 0 ,"page": 1 ,', bytes(rows[0]["body"]))  # the bytes as sent, not re-encoded
        self.assertEqual(rows[0]["request_key"], {"account": ACCOUNT, "fr_dt": "20261001", "to_dt": "20261022"})
        self.assertEqual(bronze.stats["written"], 3)
        self.assertEqual(bronze.stats["repeated"], 2)

        run = self.db.execute("SELECT run_type, status, row_count, raw_sha256, completed_at FROM ingestion_runs "
                              "WHERE run_id = ?", (capture.run_id,)).fetchone()
        combined = hashlib.sha256("\n".join(r["body_sha256"] for r in rows).encode("ascii")).hexdigest()
        self.assertEqual((run["run_type"], run["status"], run["row_count"], run["raw_sha256"]),
                         ("kiwoom_rest_bronze", "success", 3, combined))
        self.assertTrue(run["completed_at"])

        stored = self.db.execute("SELECT string_agg(encode(body, 'escape') || request_key::text, ' ') AS text "
                                 "FROM bronze.kiwoom_responses").fetchone()["text"]
        for secret in (APP_KEY, APP_SECRET, TOKEN, "Bearer"):
            self.assertNotIn(secret, stored)

    def test_without_the_table_capture_switches_off_and_the_inquiry_still_answers(self):
        bronze = kiwoom_bronze.KiwoomBronze(self._connect, TENANT)
        self.addCleanup(bronze.close)
        capture = bronze.for_target(self._target())
        client = self._client()
        client.set_capture(capture)
        with unittest.mock.patch.object(kiwoom_bronze, "TABLE", "bronze.kiwoom_responses_missing"):
            with self.assertLogs(kiwoom_bronze.LOGGER, "WARNING"):
                self.assertEqual(len(client.inquire("ust21100", {"fr_dt": "20261001"})), 2)
        capture.finish(True)
        self.assertFalse(bronze.enabled)
        run = self.db.execute("SELECT status, row_count FROM ingestion_runs WHERE run_id = ?",
                              (capture.run_id,)).fetchone()
        self.assertEqual((run["status"], run["row_count"]), ("failed", 0))


if __name__ == "__main__":
    unittest.main()
