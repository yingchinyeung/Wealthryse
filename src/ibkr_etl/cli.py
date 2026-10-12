from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

from .config import load_settings
from .db import connect, initialize
from .flex_guard import circuit_status, reset_circuit
from .reconcile import reconcile_run
from .service import (
    backfill_daily_position_snapshots,
    backfill_windows,
    daily_window,
    fetch_and_ingest,
    ingest_content,
    monthly_windows,
    status,
)


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("dates must use YYYY-MM-DD") from exc


def _configure_logging(log_dir: Path, verbose: bool) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    handlers.append(logging.FileHandler(log_dir / "ibkr_etl.log", encoding="utf-8"))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ibkr-etl",
        description="Download and load IBKR Flex activity statements.",
    )
    parser.add_argument("--env-file", type=Path, help="Alternative .env file")
    parser.add_argument("--verbose", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("init-db", help="Create or upgrade the local database")
    commands.add_parser("daily", help="Import the configured rolling daily window")

    backfill = commands.add_parser("backfill", help="Import a historical date range")
    backfill.add_argument("--from", dest="from_date", type=_date, required=True)
    backfill.add_argument("--to", dest="to_date", type=_date, required=True)

    monthly = commands.add_parser(
        "monthly-snapshots",
        help="Re-query each calendar month to build historical period-end positions",
    )
    monthly.add_argument("--from", dest="from_date", type=_date, required=True)
    monthly.add_argument("--to", dest="to_date", type=_date, required=True)

    daily_positions = commands.add_parser(
        "daily-position-snapshots",
        help="Backfill exact daily positions for source NAV dates that are missing them",
    )
    daily_positions.add_argument("--from", dest="from_date", type=_date, required=True)
    daily_positions.add_argument("--to", dest="to_date", type=_date, required=True)
    daily_positions.add_argument("--account", required=True)
    daily_positions.add_argument(
        "--pause-seconds",
        type=float,
        default=10.0,
        help="Delay between requests; defaults to the safety interval of 10 seconds",
    )
    daily_positions.add_argument(
        "--max-dates",
        type=int,
        help="Optional batch size; rerunning automatically skips completed dates",
    )

    import_file = commands.add_parser("import-file", help="Load a downloaded Flex XML file")
    import_file.add_argument("path", type=Path)

    reconcile = commands.add_parser("reconcile", help="Re-run checks for an ingestion run")
    reconcile.add_argument("--run-id", type=int)

    show_status = commands.add_parser("status", help="Show recent ingestion runs")
    show_status.add_argument("--limit", type=int, default=10)
    commands.add_parser("flex-circuit-status", help="Show the current token safety circuit")
    reset = commands.add_parser(
        "reset-flex-circuit",
        help="Clear a circuit only after credentials or an IBKR lockout are resolved",
    )
    reset.add_argument("--confirm", action="store_true", required=True)
    return parser


def _print(value: object) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings(args.env_file)
    _configure_logging(settings.log_dir, args.verbose)

    try:
        if args.command == "init-db":
            initialize(
                settings.database_path,
                settings.database_url,
                settings.tenant_id,
                settings.tenant_name,
            )
            _print({"status": "ok", "database_backend": settings.database_backend})
            return 0

        if args.command == "daily":
            from_date, to_date = daily_window(settings)
            _print(fetch_and_ingest(settings, from_date, to_date, "daily"))
            return 0

        if args.command == "backfill":
            results = []
            for from_date, to_date in backfill_windows(args.from_date, args.to_date):
                results.append(fetch_and_ingest(settings, from_date, to_date, "backfill"))
            _print(results)
            return 0

        if args.command == "monthly-snapshots":
            results = []
            for from_date, to_date in monthly_windows(args.from_date, args.to_date):
                results.append(fetch_and_ingest(settings, from_date, to_date, "monthly-snapshot"))
            _print(results)
            return 0

        if args.command == "daily-position-snapshots":
            _print(
                backfill_daily_position_snapshots(
                    settings,
                    args.from_date,
                    args.to_date,
                    args.account,
                    pause_seconds=args.pause_seconds,
                    max_dates=args.max_dates,
                )
            )
            return 0

        if args.command == "import-file":
            path = args.path.resolve()
            if not path.is_file():
                raise FileNotFoundError(path)
            _print(ingest_content(settings, path.read_bytes(), run_type="import-file"))
            return 0

        if args.command == "reconcile":
            initialize(
                settings.database_path,
                settings.database_url,
                settings.tenant_id,
                settings.tenant_name,
            )
            connection = connect(
                settings.database_path, settings.database_url, settings.tenant_id
            )
            try:
                run_id = args.run_id
                if run_id is None:
                    row = connection.execute(
                        "SELECT run_id FROM ingestion_runs WHERE status = 'success' ORDER BY run_id DESC LIMIT 1"
                    ).fetchone()
                    if row is None:
                        raise ValueError("No successful ingestion run exists")
                    run_id = int(row[0])
                _print(reconcile_run(connection, run_id))
            finally:
                connection.close()
            return 0

        if args.command == "status":
            _print(status(settings, args.limit))
            return 0
        if args.command == "flex-circuit-status":
            _print(circuit_status(settings))
            return 0
        if args.command == "reset-flex-circuit":
            _print(reset_circuit(settings))
            return 0
        return 2
    except Exception as exc:
        logging.getLogger(__name__).exception("Command failed")
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
