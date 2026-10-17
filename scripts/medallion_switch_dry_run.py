"""One-day check before v2 pulls v1.1's Flex query itself (medallion S5).

Loads v1.1's archived raw Flex statements with v2's own ingest code into a
temporary SQLite database and compares the result with the v1.1 snapshots
account-sync imported for the same date. With --live-check it also reads the
live database (read-only) to see whether the first v2 pull would add rows.
Makes no IBKR request and writes nothing except stdout and the JSON report.
An account in the statements without a snapshot fails the check unless it is
named with --allow-unchecked-account; it is still live-checked.

Exit codes: 0 safe (PASS), 1 differences found (FAIL), 2 could not run.

Production, inside the platform image (blob reads use the job identity):
  python scripts/medallion_switch_dry_run.py --as-of 2026-10-20 --days 8 \
      --snapshot-date 2026-10-20 --live-check --report /tmp/dry-run.json
Local files:
  python scripts/medallion_switch_dry_run.py --statement-file a.xml \
      --snapshot-file account.sqlite --report dry-run.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.db import DEFAULT_TENANT_ID  # noqa: E402
from ibkr_etl.switch_dry_run import (  # noqa: E402
    DryRunError, mask_account_ids, masked_report, open_read_only, read_snapshot, run_dry_run,
    statement_from_bytes, summary_lines,
)

MAX_BLOB_BYTES = 512 * 1024 * 1024


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--statement-file", action="append", default=[], help="local raw Flex XML")
    parser.add_argument("--statement-blob", action="append", default=[], help="blob name in the statement container")
    parser.add_argument("--statement-blob-prefix", action="append", default=[],
                        help="every .xml blob under this prefix")
    parser.add_argument("--as-of", help="snapshot date D; with --days adds the archive folders D-days+1..D")
    parser.add_argument("--days", type=int, default=1, help="archive day folders to read (8 covers 14 days)")
    parser.add_argument("--archive-root", default="v11-archive/{tenant}",
                        help="archive folder before YYYY/MM/DD (default v11-archive/{tenant})")
    parser.add_argument("--statement-container", default="ibkr-raw")
    parser.add_argument("--snapshot-file", action="append", default=[], help="local v1.1 account snapshot")
    parser.add_argument("--snapshot-blob", action="append", default=[], help="blob name in the snapshot container")
    parser.add_argument("--snapshot-date", help="every daily/<account>/<date>/ snapshot for this date")
    parser.add_argument("--snapshot-container", default="input-snapshots")
    parser.add_argument("--storage-account-url", default=os.environ.get("AZURE_STORAGE_ACCOUNT_URL", ""))
    parser.add_argument("--tenant-id", default=os.environ.get("PORTFOLIO_TENANT_ID", DEFAULT_TENANT_ID))
    parser.add_argument("--live-check", action="store_true",
                        help="also read DATABASE_URL in a read-only transaction")
    parser.add_argument("--lookback-days", type=int, default=14)
    parser.add_argument("--allow-unchecked-account", action="append", default=[],
                        help="an account in the statements without a snapshot that the pull may load "
                             "unchecked (full id or the masked ***123 form); repeatable")
    parser.add_argument("--report", default="medallion-switch-dry-run.json", help="JSON report path")
    return parser.parse_args(argv)


def _blobs(args: argparse.Namespace):
    from ibkr_etl.cloud_storage import download_blob_bytes, list_blobs

    if not args.storage_account_url:
        raise DryRunError("Blob inputs need --storage-account-url or AZURE_STORAGE_ACCOUNT_URL")
    return (lambda container, name: download_blob_bytes(
        args.storage_account_url, container, name, maximum_bytes=MAX_BLOB_BYTES),
        lambda container, prefix: [item["name"] for item in list_blobs(args.storage_account_url, container, prefix)])


def _statement_prefixes(args: argparse.Namespace) -> list[str]:
    prefixes = list(args.statement_blob_prefix)
    if args.as_of:
        if args.days < 1:
            raise DryRunError("--days must be at least 1")
        root = args.archive_root.format(tenant=args.tenant_id).strip("/")
        end = date.fromisoformat(args.as_of)
        prefixes += [f"{root}/{end - timedelta(days=offset):%Y/%m/%d}/" for offset in range(args.days)]
    return prefixes


def _inputs(args: argparse.Namespace, work_dir: Path):
    statements, snapshots = [], []
    for name in args.statement_file:
        statements.append(statement_from_bytes(Path(name).name, Path(name).read_bytes()))
    for name in args.snapshot_file:
        snapshots.append(read_snapshot(Path(name).name, Path(name)))
    prefixes = _statement_prefixes(args)
    if args.statement_blob or prefixes or args.snapshot_blob or args.snapshot_date:
        download, listing = _blobs(args)
        names = list(args.statement_blob)
        for prefix in prefixes:
            found = sorted(name for name in listing(args.statement_container, prefix) if name.endswith(".xml"))
            if not found:
                raise DryRunError(f"No statement found under {prefix}")
            names += found
        for name in dict.fromkeys(names):
            statements.append(statement_from_bytes(name, download(args.statement_container, name)))
        snapshot_names = list(args.snapshot_blob)
        if args.snapshot_date:
            date.fromisoformat(args.snapshot_date)
            found = sorted(
                name for name in listing(args.snapshot_container, "daily/")
                if name.endswith(".sqlite") and name.split("/")[2:3] == [args.snapshot_date]
            )
            if not found:
                raise DryRunError(f"No snapshot found for {args.snapshot_date}")
            snapshot_names += found
        for index, name in enumerate(dict.fromkeys(snapshot_names)):
            local = work_dir / f"snapshot-{index:03d}.sqlite"
            local.write_bytes(download(args.snapshot_container, name))
            snapshots.append(read_snapshot(name, local))
    return statements, snapshots


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        open_live = None
        if args.live_check:
            database_url = os.environ.get("DATABASE_URL", "")
            if not database_url:
                raise DryRunError("--live-check needs DATABASE_URL")
            # Opened by run_dry_run after the offline load, so no transaction waits on it.
            open_live = lambda: open_read_only(database_url, args.tenant_id)  # noqa: E731
        with tempfile.TemporaryDirectory(prefix="medallion-dry-run-") as directory:
            work_dir = Path(directory)
            statements, snapshots = _inputs(args, work_dir)
            report = run_dry_run(statements, snapshots, work_dir=work_dir / "work",
                                 tenant_id=args.tenant_id, open_live=open_live,
                                 lookback_days=args.lookback_days,
                                 allow_unchecked=args.allow_unchecked_account)
    except Exception as error:  # noqa: BLE001 - one clear line, then exit 2
        print(f"medallion switch dry run: ERROR {type(error).__name__}: {mask_account_ids(str(error))}")
        return 2
    report, masker = masked_report(report)
    Path(args.report).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    for line in summary_lines(report):
        print(line)
    print(json.dumps({
        "status": report["status"], "report": str(Path(args.report).resolve()),
        "accounts": {account: item["status"] for account, item in sorted(report["accounts"].items())},
        "live_check": (report.get("live_check") or {}).get("status"),
    }, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
