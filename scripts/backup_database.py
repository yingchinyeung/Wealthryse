from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT_ROOT / "data" / "portfolio.db"
BACKUP_DIR = PROJECT_ROOT / "backups"
RETENTION_COUNT = 30


def main() -> int:
    if not SOURCE.is_file():
        raise FileNotFoundError(SOURCE)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    final_path = BACKUP_DIR / f"portfolio_daily_{timestamp}.db"
    temporary_path = BACKUP_DIR / f".{final_path.name}.tmp"

    source = sqlite3.connect(str(SOURCE), timeout=30)
    destination = sqlite3.connect(str(temporary_path))
    try:
        source.backup(destination)
        integrity = destination.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"Backup integrity check failed: {integrity}")
    finally:
        destination.close()
        source.close()

    temporary_path.replace(final_path)
    backups = sorted(
        BACKUP_DIR.glob("portfolio_daily_*.db"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for expired in backups[RETENTION_COUNT:]:
        expired.unlink()
    print(f"Verified database backup: {final_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

