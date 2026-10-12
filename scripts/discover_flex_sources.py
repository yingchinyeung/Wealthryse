from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.flex_discovery import run_one_validation  # noqa: E402


if __name__ == "__main__":
    print(json.dumps(run_one_validation(), sort_keys=True))
