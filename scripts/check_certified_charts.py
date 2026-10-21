"""Rehearse the certified charts on a restored copy: read-only, counts only.

Runs the certified build's chart readers (ibkr_etl.certified_charts) for each subject's newest
NAV dates (--dates, default 5; 0 for every date) until the time limit, without writing anything,
and prints counts: rows by figure, contract rule failures, new, changed and unchanged against the
published figures, same inputs with a new answer, page disagreements on a second read, reader
errors, what the website's charts=on would show on each subject's newest date, and the rows the
whole history needs. Subjects are masked (u…01); no values, no account numbers.

    DATABASE_URL=postgresql://<web login on the copy>/... python scripts/check_certified_charts.py

Use the website's read-only login: every read runs in a read-only transaction. It needs no
setting: CERTIFIED_FIGURES_PAGES is cleared for the run, so the pages answer the old way.
docs/MEDALLION_CERTIFIED_FIGURES.md ("The charts") says what the rehearsal must show.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl import certified_figures as cf  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rehearse the certified charts on a copy (read-only, counts only).")
    parser.add_argument("--subject", action="append", default=[], help="Only this subject (repeatable).")
    parser.add_argument("--dates", type=int, default=5, help="Newest NAV dates per subject (0: every date).")
    parser.add_argument("--minutes", type=float, default=None, help="Stop reading after this many minutes.")
    args = parser.parse_args(argv)
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise SystemExit("DATABASE_URL must be a PostgreSQL connection URL")
    os.environ[cf.PAGES_SETTING] = ""  # the pages answer the old way
    from ibkr_etl import certified_charts

    certified_charts.rehearse(database_url, only_subjects=args.subject, dates=max(args.dates, 0),
                              minutes=args.minutes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
