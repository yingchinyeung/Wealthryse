from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.config import load_settings  # noqa: E402
from ibkr_etl.db import connect, initialize  # noqa: E402
from ibkr_etl.derived_portfolio import (  # noqa: E402
    refresh_alpha_world_ms_portfolio,
    refresh_alpha_world_t_portfolio,
    refresh_hong_kong_china_portfolio,
)
from ibkr_etl.market_data import refresh_alpha_world_market_data  # noqa: E402


def main() -> int:
    settings = load_settings()
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
        market_data = refresh_alpha_world_market_data(connection)
        hong_kong_china = refresh_hong_kong_china_portfolio(
            connection,
            PROJECT_ROOT / "data" / "reference_data.json",
        )
        alpha_world_ms = refresh_alpha_world_ms_portfolio(connection)
        alpha_world_t = refresh_alpha_world_t_portfolio(connection)
    finally:
        connection.close()
    print(
        json.dumps(
            {
                "market_data": market_data,
                "portfolios": {
                    "hong-kong-china": hong_kong_china,
                    "alpha-world-ms": alpha_world_ms,
                    "alpha-world-t": alpha_world_t,
                },
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
