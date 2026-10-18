"""Build the certified figures (medallion upgrade U5, docs/MEDALLION_CERTIFIED_FIGURES.md).

Does nothing unless CERTIFIED_FIGURES_BUILD=on: no database connection and no lineage run.
When on, it reads every page's figures for the dates due (new NAV dates and the last five),
then for older NAV dates with no published figures yet, newest first (every older date with
--backfill), until its time limit (--minutes); checks them; and publishes them to
gold.certified_figures or holds the build with its reason. The next run carries on with the
dates left. --dry-run reads and checks but writes nothing and prints only counts. The work is
in ibkr_etl.certified_build.

Exit codes: 0 when published, held for review, not due yet or off; 1 when the run fails.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl import certified_build, lineage  # noqa: E402
from ibkr_etl import certified_figures as cf  # noqa: E402

JOB_NAME = certified_build.JOB_NAME  # the name run_main records, written out below


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build, check and publish the certified figures.")
    parser.add_argument("--backfill", action="store_true",
                        help="Read every older NAV date again, not only those with no published figures yet.")
    parser.add_argument("--minutes", type=float, default=None,
                        help="Stop reading after this many minutes and publish what was read (default: "
                             f"{certified_build.MINUTES_ENV}, else {certified_build.DEFAULT_MINUTES:g}).")
    parser.add_argument("--dry-run", action="store_true", help="Read and check, write nothing, print counts.")
    parser.add_argument("--subject", action="append", default=[], help="Only this subject (repeatable).")
    parser.add_argument("--page", action="append", default=[], choices=list(certified_build.READERS),
                        help="Only this page's figures (repeatable).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("DATABASE_URL must be a PostgreSQL connection URL")
    # The build reads the pages the old way, never from the figures it stores.
    os.environ[cf.PAGES_SETTING] = ""
    record = lineage.current()
    return certified_build.run(database_url=database_url, backfill=args.backfill, dry_run=args.dry_run,
                               only_subjects=args.subject, pages=args.page or None, minutes=args.minutes,
                               on_connect=lambda connection: record.bind(connection), record=record)


def entry(argv: list[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    if not certified_build.enabled(os.environ if environ is None else environ):
        print(f"Certified figures build is off ({cf.BUILD_SETTING} is not on); nothing done.", flush=True)
        return 0
    return lineage.run_main("ibkrv2stg-certified-figures", lambda: main(argv))


if __name__ == "__main__":
    raise SystemExit(entry(sys.argv[1:]))
