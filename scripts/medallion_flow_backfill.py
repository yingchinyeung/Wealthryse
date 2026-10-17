"""Backfill the Transfers and CashTransactions Flex rows of v1.1-imported accounts.

account-sync never imported these two sections, so external flows (deposits,
withdrawals, transfers) are invisible for those accounts' history. This reads
v1.1's archived raw statements, loads only those sections with v2's parser,
and reports what would be added and which flows that creates.

Dry run by default: nothing is written except the JSON report. --live reads
DATABASE_URL read-only to skip rows already there and list frozen periods.
--apply inserts missing rows in one transaction (never overwrites); if the
rows add an external flow it also needs --accept-flow-changes, because live
returns for those dates would then change. Every account must belong to the
tenant (account_belongs_to_current_tenant), checked with --live and again
inside the --apply transaction.

Exit codes: 0 no flow added (or applied), 1 flows would be added, 2 could not run.

Production, inside the platform image:
  python scripts/medallion_flow_backfill.py --accounts-from-registry --live \\
      --report /tmp/flow-backfill.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.db import DEFAULT_TENANT_ID  # noqa: E402
from ibkr_etl.flow_backfill import (  # noqa: E402
    BackfillError, apply, load_candidates, plan, registry_accounts, unowned_accounts, without_rows,
)
from ibkr_etl.switch_dry_run import (  # noqa: E402
    AccountMasker, mask_account_ids, open_read_only, statement_from_bytes,
)

MAX_BLOB_BYTES = 512 * 1024 * 1024


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--statement-file", action="append", default=[], help="local raw Flex XML")
    parser.add_argument("--statement-blob-prefix", action="append", default=[],
                        help="every .xml blob under this prefix (default v11-archive/{tenant}/ without files)")
    parser.add_argument("--statement-container", default="ibkr-raw")
    parser.add_argument("--storage-account-url", default=os.environ.get("AZURE_STORAGE_ACCOUNT_URL", ""))
    parser.add_argument("--tenant-id", default=os.environ.get("PORTFOLIO_TENANT_ID", DEFAULT_TENANT_ID))
    parser.add_argument("--account", action="append", default=[], help="account to backfill")
    parser.add_argument("--accounts-from-registry", action="store_true",
                        help="the accounts account-sync imports from v1.1: registry rows (needs --live) "
                             "plus its ACCOUNT_SYNC_TARGETS_JSON, PILOT_ACCOUNT_ID and ONBOARDING_WAVE0_ACCOUNT_ID")
    parser.add_argument("--live", action="store_true", help="compare with DATABASE_URL (read-only)")
    parser.add_argument("--apply", action="store_true", help="insert the missing rows into DATABASE_URL")
    parser.add_argument("--accept-flow-changes", action="store_true",
                        help="allow rows that add external flows (live returns change)")
    parser.add_argument("--report", default="medallion-flow-backfill.json", help="JSON report path")
    args = parser.parse_args(argv)
    if args.apply:
        args.live = True
    if args.accounts_from_registry and not args.live:
        parser.error("--accounts-from-registry needs --live")
    if not args.account and not args.accounts_from_registry:
        parser.error("name accounts with --account or --accounts-from-registry")
    if not args.statement_file and not args.statement_blob_prefix:
        args.statement_blob_prefix = [f"v11-archive/{args.tenant_id}/"]
    return args


def _sources(args: argparse.Namespace):
    """Statements without content (read again when loading) and a reader for them."""

    readers = {}
    for name in args.statement_file:
        readers[name] = lambda path=Path(name): path.read_bytes()
    if args.statement_blob_prefix:
        from ibkr_etl.cloud_storage import download_blob_bytes, list_blobs

        if not args.storage_account_url:
            raise BackfillError("Blob inputs need --storage-account-url or AZURE_STORAGE_ACCOUNT_URL")
        for prefix in args.statement_blob_prefix:
            found = sorted(item["name"] for item in list_blobs(args.storage_account_url, args.statement_container, prefix)
                           if item["name"].endswith(".xml"))
            if not found:
                raise BackfillError(f"No statement found under {prefix}")
            for name in found:
                readers[name] = lambda name=name: download_blob_bytes(
                    args.storage_account_url, args.statement_container, name, maximum_bytes=MAX_BLOB_BYTES)
    statements = [replace(statement_from_bytes(name, read()), content=b"") for name, read in readers.items()]
    return statements, lambda statement: readers[statement.name]()


def _configured_accounts() -> set[str]:
    """account-sync's own environment targets, when this job is given the same settings."""

    accounts = {os.environ.get(name, "").strip().upper() for name in ("PILOT_ACCOUNT_ID", "ONBOARDING_WAVE0_ACCOUNT_ID")}
    for item in json.loads(os.environ.get("ACCOUNT_SYNC_TARGETS_JSON", "[]") or "[]"):
        accounts.add(str(item["account_id"]).strip().upper())
    return {account for account in accounts if account}


def _connect(args: argparse.Namespace):
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise BackfillError("--live and --apply need DATABASE_URL")
    if not args.apply:
        return open_read_only(database_url, args.tenant_id)
    import psycopg

    connection = psycopg.connect(database_url)
    connection.execute("SELECT set_config('app.tenant_id', %s, true)", (args.tenant_id,))
    return connection


def run(args: argparse.Namespace, work_dir: Path) -> dict:
    accounts = set(args.account)
    if args.live:
        # A short read: registry accounts, and every selected account must belong to the tenant.
        live = _connect(args)
        try:
            if args.accounts_from_registry:
                accounts |= set(registry_accounts(live, args.tenant_id)) | _configured_accounts()
            unowned = unowned_accounts(live, accounts)
        finally:
            live.rollback()
            live.close()
        if unowned:
            raise BackfillError(f"Accounts not in this tenant: {', '.join(unowned)}")
    accounts = sorted(accounts)
    if not accounts:
        raise BackfillError("No account to backfill")
    statements, read = _sources(args)
    candidates = load_candidates(statements, read, accounts=accounts, work_dir=work_dir)
    # Connect only now: loading the archive can take minutes.
    live = _connect(args) if args.live else None
    try:
        planned = plan(candidates, accounts=accounts, live=live, tenant_id=args.tenant_id)
        report = {"mode": "apply" if args.apply else "dry_run", "statements": len(statements),
                  **without_rows(planned)}
        if args.apply:
            report["applied"] = apply(live, planned, tenant_id=args.tenant_id,
                                      accept_flow_changes=args.accept_flow_changes,
                                      requested=(min((s.from_date for s in statements), default=None),
                                                 max((s.to_date for s in statements), default=None)))
        return report
    finally:
        if live is not None:
            live.rollback()
            live.close()


def _summary(report: dict) -> list[str]:
    lines = [f"medallion flow backfill: {report['mode']} over {report['statements']} statements, "
             f"{report['new_rows']} new rows, flows added: {'yes' if report['flows_added'] else 'no'}"]
    for account, item in sorted(report["accounts"].items()):
        lines.append(
            f"  {account}: candidates={item['candidate_rows']} new={item['new_rows']} "
            f"live_same={item['already_live_same']} live_different={item['already_live_different']} "
            f"new_transfers={item['new_transfers']} new_external_cash={item['new_external_cash_transactions']} "
            f"flow_days={len(item['flows_added'])} frozen_periods_affected="
            f"{len(item['frozen_periods_that_would_differ_from_live'])}")
    if "applied" in report:
        lines.append(f"  applied: {report['applied']['status']} inserted={report['applied']['inserted']}")
    return lines


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        with tempfile.TemporaryDirectory(prefix="medallion-flow-backfill-") as directory:
            report = run(args, Path(directory))
    except Exception as error:  # noqa: BLE001 - one clear line, then exit 2
        print(f"medallion flow backfill: ERROR {type(error).__name__}: {mask_account_ids(str(error))}")
        return 2
    masker = AccountMasker(report["accounts"])
    report = json.loads(masker.scrub(json.dumps(report, sort_keys=True, default=str)))
    Path(args.report).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    for line in _summary(report):
        print(line)
    if args.apply:
        return 0
    return 1 if report["flows_added"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
