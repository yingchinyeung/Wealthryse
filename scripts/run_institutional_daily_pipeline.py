from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    for script in (
        "dispatch_institutional_daily_work.py",
        "run_institutional_flex_worker.py",
        # Does nothing unless KIWOOM_COLLECTOR_ENABLED=true.
        "run_kiwoom_collector.py",
    ):
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / script)],
            cwd=ROOT,
            check=True,
            timeout=2700,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
