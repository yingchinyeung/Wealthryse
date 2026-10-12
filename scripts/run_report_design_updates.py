"""Dedicated scheduled worker; never blocks the normal month-end scheduler."""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / 'src'))

from ibkr_etl.db import connect
from ibkr_etl.report_design_worker import process_next_update
from ibkr_etl.util import canonical_json


def main():
    if os.environ.get('REPORT_DESIGN_UPDATES_ENABLED') != 'true':
        print(canonical_json({'status': 'disabled'}))
        return 0
    database_url = os.environ.get('DATABASE_URL', '')
    if (os.environ.get('DEPLOYMENT_ENVIRONMENT') != 'internal-production'
            or not database_url.startswith(('postgres://', 'postgresql://'))):
        raise RuntimeError('Design regeneration requires the qualified PostgreSQL reporting runtime')
    tenant = os.environ.get('PORTFOLIO_TENANT_ID', '00000000-0000-0000-0000-000000000001')
    connection = connect(Path('/tmp/unused.sqlite3'), database_url, tenant)
    try:
        print(canonical_json(process_next_update(connection, tenant, PROJECT_ROOT)))
    finally:
        connection.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
