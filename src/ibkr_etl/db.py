from __future__ import annotations

import os
import sqlite3
import hashlib
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .util import utc_now_iso


ROOT = Path(
    os.environ.get("IBKR_PROJECT_ROOT", Path(__file__).resolve().parents[2])
).resolve()
SQLITE_MIGRATION = ROOT / "migrations" / "001_init.sql"
POSTGRES_MIGRATIONS_DIR = ROOT / "migrations" / "postgres"
DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"


def apply_postgres_migrations(connection) -> list[str]:
    """Apply ordered PostgreSQL migrations once and reject checksum drift."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            sha256 TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    applied: list[str] = []
    for path in sorted(POSTGRES_MIGRATIONS_DIR.glob("*.sql")):
        version = path.name
        content = path.read_text(encoding="utf-8")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        existing = connection.execute(
            "SELECT sha256 FROM schema_migrations WHERE version = ?", (version,)
        ).fetchone()
        if existing is not None:
            if existing[0] != digest:
                raise RuntimeError(
                    f"Applied PostgreSQL migration checksum changed: {version}"
                )
            continue
        connection.execute(content)
        connection.execute(
            "INSERT INTO schema_migrations(version, sha256, applied_at) VALUES (?, ?, ?)",
            (version, digest, utc_now_iso()),
        )
        applied.append(version)
    return applied


class HybridRow(Mapping[str, Any]):
    """A DB-API row supporting both row[0] and row['column']."""

    def __init__(self, names: Sequence[str], values: Sequence[Any]) -> None:
        self._names = tuple(names)
        self._values = tuple(values)
        self._mapping = dict(zip(self._names, self._values))

    def __getitem__(self, key: str | int) -> Any:
        return self._values[key] if isinstance(key, int) else self._mapping[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._names)

    def __len__(self) -> int:
        return len(self._names)

    def keys(self):
        return self._mapping.keys()


def _hybrid_row_factory(cursor):
    description = cursor.description or ()
    names = [column.name for column in description]
    return lambda values: HybridRow(names, values)


class PostgresCursor:
    def __init__(self, cursor) -> None:
        self._cursor = cursor

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def __iter__(self):
        return iter(self._cursor)

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description


class PostgresConnection:
    backend = "postgresql"

    def __init__(self, connection) -> None:
        self._connection = connection

    @staticmethod
    def _translate(sql: str) -> str:
        return sql.replace("?", "%s")

    def execute(self, sql: str, parameters: Sequence[Any] | None = None) -> PostgresCursor:
        if parameters is None:
            cursor = self._connection.execute(self._translate(sql), prepare=False)
        else:
            cursor = self._connection.execute(
                self._translate(sql), tuple(parameters), prepare=False
            )
        return PostgresCursor(cursor)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def transaction(self):
        # psycopg's block: a savepoint inside an open transaction, which the caller commits.
        return self._connection.transaction()

    def close(self) -> None:
        self._connection.close()


def is_postgres(connection: Any) -> bool:
    return getattr(connection, "backend", "sqlite") == "postgresql"


def connect(
    database_path: Path,
    database_url: str = "",
    tenant_id: str = DEFAULT_TENANT_ID,
):
    if database_url.startswith(("postgres://", "postgresql://")):
        try:
            import psycopg
        except ImportError as error:
            raise RuntimeError(
                "PostgreSQL mode requires psycopg. Install the project with its dependencies."
            ) from error
        raw = psycopg.connect(database_url, row_factory=_hybrid_row_factory)
        connection = PostgresConnection(raw)
        connection.execute("SELECT set_config('app.tenant_id', ?, false)", (tenant_id,))
        connection.commit()
        return connection

    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def initialize(
    database_path: Path,
    database_url: str = "",
    tenant_id: str = DEFAULT_TENANT_ID,
    tenant_name: str = "Own Portfolio",
) -> None:
    if database_url.startswith(("postgres://", "postgresql://")):
        connection = connect(database_path, database_url, tenant_id)
        try:
            migrations_enabled = os.environ.get(
                "IBKR_RUN_MIGRATIONS", "true"
            ).strip().lower() in {"1", "true", "yes", "on"}
            if migrations_enabled:
                apply_postgres_migrations(connection)
            elif connection.execute(
                "SELECT to_regclass('public.tenants')"
            ).fetchone()[0] is None:
                raise RuntimeError(
                    "PostgreSQL schema is not initialized. Run bootstrap_postgres.py first."
                )
            connection.execute(
                """
                INSERT INTO tenants(tenant_id, name, status, created_at)
                VALUES (?, ?, 'active', ?)
                ON CONFLICT(tenant_id) DO UPDATE SET name = excluded.name
                """,
                (tenant_id, tenant_name, utc_now_iso()),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return

    sql = SQLITE_MIGRATION.read_text(encoding="utf-8")
    connection = connect(database_path)
    try:
        connection.executescript(sql)
    finally:
        connection.close()


@contextmanager
def transaction(connection) -> Iterator[Any]:
    try:
        if not is_postgres(connection):
            connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def start_run(
    connection,
    run_type: str,
    requested_from: str | None,
    requested_to: str | None,
) -> int:
    sql = """
        INSERT INTO ingestion_runs(
            run_type, requested_from, requested_to, started_at, status
        ) VALUES (?, ?, ?, ?, 'running')
    """
    parameters = (run_type, requested_from, requested_to, utc_now_iso())
    if is_postgres(connection):
        row = connection.execute(sql + " RETURNING run_id", parameters).fetchone()
        run_id = int(row[0])
    else:
        cursor = connection.execute(sql, parameters)
        run_id = int(cursor.lastrowid)
    connection.commit()
    return run_id


def finish_run_success(
    connection,
    run_id: int,
    *,
    reference_code: str | None,
    raw_sha256: str,
    raw_path: str,
    row_count: int,
    reconciliation_json: str,
) -> None:
    connection.execute(
        """
        UPDATE ingestion_runs
        SET completed_at = ?, status = 'success', reference_code = ?,
            raw_sha256 = ?, raw_path = ?, row_count = ?, reconciliation_json = ?
        WHERE run_id = ?
        """,
        (
            utc_now_iso(), reference_code, raw_sha256, raw_path, row_count,
            reconciliation_json, run_id,
        ),
    )
    connection.commit()


def finish_run_failure(connection, run_id: int, message: str) -> None:
    connection.execute(
        """
        UPDATE ingestion_runs
        SET completed_at = ?, status = 'failed', error_message = ?
        WHERE run_id = ?
        """,
        (utc_now_iso(), message[:4000], run_id),
    )
    connection.commit()
