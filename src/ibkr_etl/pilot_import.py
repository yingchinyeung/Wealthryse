from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from .pilot_snapshot import ACCOUNT_TABLES, validate_pilot_snapshot
from .util import utc_now_iso


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _upsert(connection, table: str, rows: list[dict], keys: tuple[str, ...]) -> int:
    if not rows:
        return 0
    from psycopg import sql

    columns = list(rows[0])
    updates = [column for column in columns if column not in keys]
    query = sql.SQL(
        "INSERT INTO {} ({}) VALUES ({}) ON CONFLICT ({}) DO UPDATE SET {}"
    ).format(
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, columns)),
        sql.SQL(", ").join(sql.Placeholder() for _ in columns),
        sql.SQL(", ").join(map(sql.Identifier, keys)),
        sql.SQL(", ").join(
            sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(column), sql.Identifier(column))
            for column in updates
        ),
    )
    with connection.cursor() as cursor:
        cursor.executemany(query, [[row[column] for column in columns] for row in rows])
    return len(rows)


def import_snapshot(
    *,
    database_url: str,
    local_path: Path,
    account_id: str,
    start_date: str,
    tenant_id: str,
    source_reference: str,
) -> dict:
    """Validate and idempotently import one account-only transfer snapshot."""
    validation = validate_pilot_snapshot(
        local_path, account_id=account_id, start_date=start_date
    )
    # Fail closed until the v3 evidence importer has passed a rollback-only
    # PostgreSQL rehearsal. Never silently discard evidence or reuse source IDs.
    if validation["metadata"]["format_version"] != "2":
        raise RuntimeError("Evidence-preserving v3 import is not enabled; staging acceptance is required")
    digest = file_sha256(local_path)

    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(database_url, row_factory=dict_row) as target:
        target.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id,))
        existing = target.execute(
            "SELECT run_id FROM ingestion_runs WHERE run_type = 'staging-snapshot-import' AND raw_sha256 = %s AND status = 'success'",
            (digest,),
        ).fetchone()
        if existing:
            return {
                "status": "PASS",
                "result": "already_imported",
                "run_id": existing["run_id"],
                "sha256": digest,
                "coverage": validation["coverage"],
                "financial_evidence": validation["financial_evidence"],
            }
        run_id = target.execute(
            """
            INSERT INTO ingestion_runs(run_type, requested_from, requested_to, started_at, status, raw_sha256, raw_path, tenant_id)
            VALUES ('staging-snapshot-import', %s, %s, %s, 'running', %s, %s, %s)
            RETURNING run_id
            """,
            (
                start_date,
                max(
                    (value["last"] for value in validation["coverage"].values() if value["last"]),
                    default=start_date,
                ),
                utc_now_iso(),
                digest,
                source_reference,
                tenant_id,
            ),
        ).fetchone()["run_id"]
        target.commit()

        source = sqlite3.connect(f"file:{local_path.as_posix()}?mode=ro", uri=True)
        source.row_factory = sqlite3.Row
        imported: dict[str, int] = {}
        try:
            account = dict(source.execute("SELECT * FROM accounts").fetchone())
            account["tenant_id"] = tenant_id
            imported["accounts"] = _upsert(
                target, "accounts", [account], ("account_id",)
            )
            instruments = [dict(row) for row in source.execute("SELECT * FROM instruments")]
            imported["instruments"] = _upsert(
                target, "instruments", instruments, ("conid",)
            )
            for table, (_, _, keys) in ACCOUNT_TABLES.items():
                rows = [dict(row) for row in source.execute(f'SELECT * FROM "{table}"')]
                for row in rows:
                    row["last_seen_run_id"] = run_id
                imported[table] = _upsert(target, table, rows, keys)

            actual: dict[str, int] = {}
            for table in ACCOUNT_TABLES:
                actual[table] = int(
                    target.execute(
                        f'SELECT COUNT(*) AS n FROM "{table}" WHERE account_id = %s',
                        (account_id,),
                    ).fetchone()["n"]
                )
            actual["accounts"] = int(
                target.execute(
                    "SELECT COUNT(*) AS n FROM accounts WHERE account_id = %s",
                    (account_id,),
                ).fetchone()["n"]
            )
            expected = validation["row_counts"]
            for table in ACCOUNT_TABLES:
                if actual[table] != expected[table]:
                    raise RuntimeError(
                        f"Reconciliation failed for {table}: {actual[table]} != {expected[table]}"
                    )
            reconciliation = {
                "status": "PASS",
                "account_id": account_id,
                "start_date": start_date,
                "source_sha256": digest,
                "imported_rows": imported,
                "target_rows": actual,
                "coverage": validation["coverage"],
                "financial_evidence": validation["financial_evidence"],
            }
            target.execute(
                """
                UPDATE ingestion_runs
                SET completed_at = %s, status = 'success', row_count = %s, reconciliation_json = %s
                WHERE run_id = %s
                """,
                (
                    utc_now_iso(),
                    sum(imported.values()),
                    json.dumps(reconciliation, sort_keys=True),
                    run_id,
                ),
            )
            target.commit()
            return {"run_id": run_id, **reconciliation}
        except Exception as error:
            target.rollback()
            target.execute(
                "UPDATE ingestion_runs SET completed_at = %s, status = 'failed', error_message = %s WHERE run_id = %s",
                (utc_now_iso(), str(error)[:4000], run_id),
            )
            target.commit()
            raise
        finally:
            source.close()
