from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.reference_data import refresh_reference_data  # noqa: E402
from ibkr_etl.config import load_settings  # noqa: E402


def main() -> int:
    settings = load_settings()
    parser = argparse.ArgumentParser(description="Refresh cached analytics reference data.")
    parser.add_argument("--max-sector-lookups", type=int, default=25)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--force-risk-free", action="store_true")
    args = parser.parse_args()
    result = refresh_reference_data(
        settings.database_path,
        PROJECT_ROOT / "data" / "reference_data.json",
        max_sector_lookups=args.max_sector_lookups,
        workers=args.workers,
        timeout=args.timeout,
        force_risk_free=args.force_risk_free,
        database_url=settings.database_url,
        tenant_id=settings.tenant_id,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
