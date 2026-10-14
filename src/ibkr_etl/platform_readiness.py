"""Bounded, read-only database readiness; never used for process liveness."""
import os
import psycopg
from fastapi.responses import JSONResponse


def database_readiness():
    url = os.environ.get('DATABASE_URL', '')
    if not url.startswith(('postgres://', 'postgresql://')):
        return JSONResponse({'status': 'not_ready'}, status_code=503,
                            headers={'Cache-Control': 'no-store'})
    try:
        with psycopg.connect(url, connect_timeout=3,
                options='-c statement_timeout=1000 -c default_transaction_read_only=on') as connection:
            if connection.execute('SELECT 1').fetchone()[0] != 1:
                raise ValueError('Readiness query did not complete')
    except Exception:
        # Never expose a connection string, host or database exception to probes.
        return JSONResponse({'status': 'not_ready'}, status_code=503,
                            headers={'Cache-Control': 'no-store'})
    return JSONResponse({'status': 'ready'}, headers={'Cache-Control': 'no-store'})
