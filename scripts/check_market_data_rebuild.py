"""Rebuild silver market data from the raw files in bronze-market-data and compare. Read-only.

    python scripts/check_market_data_rebuild.py [--days 7] [--require-files]

Prints one MARKET_DATA_REBUILD line with counts (never prices or rates) and exits 0 when no
silver row differs from what its stored raw file parses to. See ibkr_etl.market_data_rebuild.
Needs DATABASE_URL, AZURE_STORAGE_ACCOUNT_URL and MARKET_DATA_RAW_CONTAINER, as the
market-data jobs have them.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.market_data_rebuild import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
