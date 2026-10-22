"""Fill and check the "as known on" history of the silver IBKR tables (review item U8).

docs/MEDALLION_AS_KNOWN_ON.md has the design. Run by hand only, first on a restored copy,
after migration 074 (migrations/postgres/074_silver_history.sql) has been applied:

  backfill      replay every kept raw file in the order it reached silver, compare the end
                state with the live tables, and report counts. Dry run unless --apply, which
                inserts the versions and turns recording on in one transaction, then checks.
  check         each table equals its current versions, versions never overlap, every
                removal has a note; with version counts by kind. Read-only.
  fx-merge      the FX step, after u8_fx_event_key.sql (075_fx_event_key.sql on its day): fill
                event_key and merge FX rows IBKR re-sent with new amounts. Dry run unless --apply,
                and safe to run again: a second --apply finds nothing to merge.
  fx-unmerge    the FX step's undo: put merged rows back. Dry run unless --apply.
  fx-check      read-only: FX events with more than one current row now and after the merge,
                the FX amounts the merge changes, and re-sends the identity rule would miss.
                Counts only. Exit 0 when no FX event has more than one current row.

The raw files: v2's statements are found by the file_path and sha256 in bronze.raw_reports,
account-sync's dated copies by the sha256 of their import run, in local folders (--raw-dir,
--snapshot-dir, any layout: files are matched by sha256) or in blob storage
(--storage-account-url: ibkr-raw by each file_path, input-snapshots under daily/).
--v11-dir or --v11-prefix adds v1.1's archived statements: the backfill reports how many
changes in the copies they explain, and fx-merge and fx-check read their FX rows too.

The database URL comes from DATABASE_ADMIN_URL (or the variable --url-env names) and is never
printed. Dry runs, check and fx-check use a read-only transaction. Output and --report hold counts and
table names only: no account numbers, amounts or names. Row-level security applies as for
scripts/medallion_layers.py, so name every tenant with --tenant (default: the default tenant).

Exit codes: 0 ready or done, 1 differences or check problems (read the report), 2 could not run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl import silver_history as history  # noqa: E402
from ibkr_etl.db import DEFAULT_TENANT_ID  # noqa: E402
from ibkr_etl.switch_dry_run import mask_account_ids  # noqa: E402

MAX_BLOB_BYTES = 512 * 1024 * 1024


def _arguments(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("backfill", "check", "fx-merge", "fx-unmerge", "fx-check"))
    parser.add_argument("--apply", action="store_true", help="write (backfill, fx-merge, fx-unmerge)")
    parser.add_argument("--accept-differences", action="store_true",
                        help="backfill: when the replay's end state differs from a live table, close the "
                             "replay's version and keep the live row as a baseline (only once explained); "
                             "fx-merge: go ahead although raw statements are missing (their rows are left)")
    parser.add_argument("--tenant", action="append", default=[], help="tenant id (repeatable)")
    parser.add_argument("--url-env", default="DATABASE_ADMIN_URL")
    parser.add_argument("--raw-dir", action="append", default=[], type=Path, help="local raw statements")
    parser.add_argument("--snapshot-dir", action="append", default=[], type=Path,
                        help="local account-sync copies")
    parser.add_argument("--v11-dir", action="append", default=[], type=Path, help="local v1.1 statements")
    parser.add_argument("--storage-account-url", default=os.environ.get("AZURE_STORAGE_ACCOUNT_URL", ""))
    parser.add_argument("--snapshot-container", default="input-snapshots")
    parser.add_argument("--statement-container", default="ibkr-raw")
    parser.add_argument("--v11-prefix", action="append", default=[],
                        help="v1.1 statements under this prefix of the statement container")
    parser.add_argument("--skip-run-type", action="append", default=[],
                        help="leave out statements loaded by this run type; their rows become baselines")
    parser.add_argument("--legacy-key-compat", action="store_true",
                        help="replay with IBKR_FLEX_LEGACY_KEY_COMPAT on, as the switch-time pull ran")
    parser.add_argument("--lock-seconds", type=float, default=history.LOCK_SECONDS)
    parser.add_argument("--work-dir", type=Path, help="keep the replay here (default: a temporary folder)")
    parser.add_argument("--report", type=Path, help="write the JSON report here (counts only)")
    return parser.parse_args(argv)


def _connect(url: str, *, read_only: bool):
    import psycopg
    from psycopg.conninfo import make_conninfo

    options = "-c statement_timeout=600000 -c lock_timeout=10000"
    if read_only:
        options += " -c default_transaction_read_only=on"
    connection = psycopg.connect(make_conninfo(url, options=options, application_name="medallion-silver-history"))
    if read_only:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    return connection


class Files:
    """Raw files by sha256, from local folders or blob storage; each one checked before use."""

    def __init__(self, folders, work_dir: Path, storage_url: str = "") -> None:
        self.folders, self.work_dir, self.storage_url = list(folders), work_dir, storage_url
        self._index: dict[str, Path] | None = None
        self.counts: dict[str, int] = {}

    def _local(self) -> dict[str, Path]:
        if self._index is None:
            self._index = {}
            for folder in self.folders:
                for path in sorted(Path(folder).rglob("*")):
                    if path.is_file():
                        self._index.setdefault(_sha256(path.read_bytes()), path)
        return self._index

    def get(self, digest: str, blob: tuple[str, str, str] | None = None) -> Path | None:
        """Path to a verified copy of the file with this sha256, or None. blob: (account url, container, name)."""
        path = self._local().get(digest)
        if path is None and blob is not None and blob[0]:
            from ibkr_etl.cloud_storage import download_blob_bytes

            content = download_blob_bytes(*blob, maximum_bytes=MAX_BLOB_BYTES)
            path = self.work_dir / digest
            path.write_bytes(content)
        if path is None:
            return None
        if _sha256(path.read_bytes()) != digest:
            raise history.HistoryError("a raw file does not match its recorded sha256")
        return path


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _blob(file_path: str) -> tuple[str, str, str] | None:
    """(account url, container, name) of a blob URL kept in raw_reports.file_path."""
    parts = urlsplit(file_path or "")
    if parts.scheme != "https" or "/" not in parts.path.strip("/"):
        return None
    container, name = parts.path.lstrip("/").split("/", 1)
    return f"{parts.scheme}://{parts.netloc}", container, unquote(name)


def _snapshot_blobs(args) -> dict[str, tuple[str, str, str]]:
    if not args.storage_account_url:
        return {}
    from ibkr_etl.cloud_storage import list_blobs

    found = {}
    for item in list_blobs(args.storage_account_url, args.snapshot_container, "daily/"):
        if item["name"].endswith(".sqlite"):
            found[Path(item["name"]).stem] = (args.storage_account_url, args.snapshot_container, item["name"])
    return found


def _v11_contents(args, work_dir: Path):
    for folder in args.v11_dir:
        for path in sorted(Path(folder).rglob("*.xml")):
            yield path.read_bytes()
    if args.v11_prefix:
        from ibkr_etl.cloud_storage import download_blob_bytes, list_blobs

        for prefix in args.v11_prefix:
            for item in sorted(list_blobs(args.storage_account_url, args.statement_container, prefix),
                               key=lambda item: item["name"]):
                if item["name"].endswith(".xml"):
                    yield download_blob_bytes(args.storage_account_url, args.statement_container, item["name"],
                                              maximum_bytes=MAX_BLOB_BYTES)


def _inputs(args, url: str, tenants: list[str], work_dir: Path, *, statements_only: bool = False):
    """Loads in order with their files, from a read-only look at the database."""
    connection = _connect(url, read_only=True)
    try:
        loads, paths, counts = [], {}, {}
        for label, tenant in history.tenant_labels(tenants).items():
            history.use_tenant(connection, tenant)
            found, tally = history.gather_loads(connection, tenant, skip_run_types=args.skip_run_type)
            loads += [load for load in found if load.kind == history.STATEMENT or not statements_only]
            paths.update(history.raw_report_paths(connection, tenant))
            counts[label] = dict(tally)
        instruments = history.live_instruments(connection)
        tables = history.tracked_with_history(connection)
    finally:
        connection.close()
    statements = Files(args.raw_dir, work_dir, args.storage_account_url)
    snapshots = Files(args.snapshot_dir, work_dir, args.storage_account_url)
    blobs = _snapshot_blobs(args) if any(load.kind == history.SNAPSHOT for load in loads) else {}
    ordered, missing = [], {"statements": 0, "snapshots": 0}
    for load in history.order_loads(loads):
        if load.kind == history.STATEMENT:
            path = statements.get(load.sha256, _blob(paths.get(load.sha256, "")))
        else:
            path = snapshots.get(load.sha256, blobs.get(load.sha256))
        if path is None:
            missing[f"{load.kind}s"] += 1
            continue
        ordered.append((load, path))
    return ordered, missing, counts, instruments, tables


def _summary(command: str, report: dict) -> list[str]:
    lines = [f"medallion silver history {command}: {report.get('status')}"]
    if command.startswith("fx-"):
        shown = {k: v for k, v in sorted(report.items()) if type(v) is int}
        if shown:
            lines.append("  statements: " + ", ".join(f"{k}={v}" for k, v in shown.items()))
    for label, entry in sorted((report.get("tenants") or {}).items()):
        for table, counts in sorted((entry.get("tables") or {}).items()):
            shown = counts.get("problems", counts)
            lines.append(f"  {label} {table}: " + ", ".join(f"{k}={v}" for k, v in sorted(shown.items())
                                                           if not isinstance(v, dict)))
    return lines


def _finish(command: str, report: dict, args) -> int:
    if args.report:
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    for line in _summary(command, report):
        print(line)
    print(json.dumps({"status": report.get("status"), "seconds": report.get("seconds")}, sort_keys=True))
    return 0 if report.get("status") in {"READY", "APPLIED", "PASS"} else 1


def cmd_backfill(args, url: str, tenants: list[str], work_dir: Path) -> int:
    started = time.monotonic()
    ordered, missing, loads, instruments, tables = _inputs(args, url, tenants, work_dir)
    if not tables:
        raise history.HistoryError("no history tables: apply 074_silver_history.sql first")
    replay = history.Replay(work_dir / "replay", tables, instruments=instruments,
                            legacy_key_compat=args.legacy_key_compat)
    try:
        for load, path in ordered:
            replay.load(load, path if load.kind == history.SNAPSHOT else path.read_bytes())
        replayed = time.monotonic()
        v11 = history.statement_digests(_v11_contents(args, work_dir)) if (args.v11_dir or args.v11_prefix) else None
        connection = _connect(url, read_only=not args.apply)
        try:
            result = history.apply_backfill(connection, replay, tenants, accept_differences=args.accept_differences,
                                            lock_seconds=args.lock_seconds, dry_run=not args.apply)
            if missing["statements"] or missing["snapshots"]:
                result["ready"] = False
                if args.apply and not args.accept_differences:
                    raise history.HistoryError(f"raw files missing: {missing}")
            if args.apply:
                connection.commit()
            else:
                connection.rollback()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        if v11 is not None:
            result["v11_statements_explain"] = dict(history.v11_explained(replay, v11))
    finally:
        replay.close()
    result.update(loads=loads, files_missing=missing, replayed=dict(replay.counts),
                  seconds={"replay": round(replayed - started, 1), "total": round(time.monotonic() - started, 1)})
    result["status"] = "APPLIED" if args.apply else ("READY" if result["ready"] else "NOT READY")
    return _finish("backfill", result, args)


def cmd_check(args, url: str, tenants: list[str], work_dir: Path) -> int:
    started = time.monotonic()
    connection = _connect(url, read_only=True)
    try:
        result = history.check(connection, tenants)
    finally:
        connection.close()
    result["status"] = "PASS" if result["ok"] else "FAIL"
    result["seconds"] = round(time.monotonic() - started, 1)
    return _finish("check", result, args)


def cmd_fx(args, url: str, tenants: list[str], work_dir: Path) -> int:
    started = time.monotonic()
    if args.command == "fx-check" and args.apply:
        raise history.HistoryError("fx-check only reads: leave out --apply")
    connection = _connect(url, read_only=not args.apply)
    missing: dict[str, int] = {}
    try:
        if args.command == "fx-check":
            result = history.fx_check(
                connection, history.fx_index(_fx_contents(args, url, tenants, work_dir, missing)), tenants)
            result["statement_files_missing"] = missing.get("statements", 0)
        elif args.command == "fx-merge":
            index = history.fx_index(_fx_contents(args, url, tenants, work_dir, missing))
            if missing.get("statements") and args.apply and not args.accept_differences:
                raise history.HistoryError(f"{missing['statements']} raw statements are missing, so nothing was "
                                           "merged; supply them, or pass --accept-differences to leave their rows")
            result = history.apply_fx_merge(connection, index, tenants, dry_run=not args.apply,
                                            lock_seconds=args.lock_seconds)
            result["statements_read"] = index.count
            result["statement_files_missing"] = missing.get("statements", 0)
        else:
            result = history.apply_fx_unmerge(connection, tenants, dry_run=not args.apply,
                                              lock_seconds=args.lock_seconds)
        if args.apply:
            connection.commit()
        else:
            connection.rollback()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    if args.command == "fx-check":
        result["status"] = "PASS" if result["ok"] else "DUPLICATES"
    else:
        result["status"] = "APPLIED" if args.apply else "READY"
    result["seconds"] = round(time.monotonic() - started, 1)
    return _finish(args.command, result, args)


def _fx_contents(args, url: str, tenants: list[str], work_dir: Path, missing: dict | None = None):
    """v1.1's statements first, then v2's in the order they were loaded; files not found counted in `missing`."""
    yield from _v11_contents(args, work_dir)
    ordered, found_missing, _, _, _ = _inputs(args, url, tenants, work_dir, statements_only=True)
    if missing is not None:
        missing.update(found_missing)
    for load, path in ordered:
        if load.kind == history.STATEMENT:
            yield path.read_bytes()


def main(argv=None) -> int:
    args = _arguments(argv)
    try:
        url = os.environ.get(args.url_env, "")
        if not url.startswith(("postgres://", "postgresql://")):
            raise history.HistoryError(f"{args.url_env} must be a PostgreSQL URL")
        tenants = args.tenant or [os.environ.get("PORTFOLIO_TENANT_ID", DEFAULT_TENANT_ID)]
        command = {"backfill": cmd_backfill, "check": cmd_check, "fx-merge": cmd_fx, "fx-unmerge": cmd_fx,
                   "fx-check": cmd_fx}
        if args.work_dir:
            args.work_dir.mkdir(parents=True, exist_ok=True)
            return command[args.command](args, url, tenants, args.work_dir)
        with tempfile.TemporaryDirectory(prefix="medallion-silver-history-") as directory:
            return command[args.command](args, url, tenants, Path(directory))
    except Exception as error:  # noqa: BLE001 - one clear line, then exit 2
        print(f"medallion silver history: ERROR {type(error).__name__}: {mask_account_ids(str(error))}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
