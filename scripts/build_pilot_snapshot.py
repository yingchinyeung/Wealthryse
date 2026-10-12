from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.pilot_snapshot import (  # noqa: E402
    build_pilot_snapshot,
    validate_pilot_snapshot,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build an account-only SQLite transfer snapshot for staging."
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--account", default="U16088571")
    parser.add_argument("--start-date", default="2025-01-20")
    parser.add_argument("--include-financial-evidence", action="store_true",
                        help="Build review-only v3 with private Flex transfer/FX evidence; live import is not enabled.")
    args = parser.parse_args()

    manifest = build_pilot_snapshot(
        args.source,
        args.output,
        account_id=args.account,
        start_date=args.start_date,
        include_financial_evidence=args.include_financial_evidence,
    )
    validation = validate_pilot_snapshot(
        args.output, account_id=args.account, start_date=args.start_date
    )
    print(json.dumps({"manifest": manifest, "validation": validation}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
