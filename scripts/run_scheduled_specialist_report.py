"""Reviewed product worker. Frozen rehearsal date is optional dispatcher input."""
import json
import os
from datetime import date
import psycopg
from psycopg.rows import dict_row
from ibkr_etl.specialist_report_runtime import validate_database_url
from ibkr_etl.specialist_archive_reader import managed_reporting_archive
from ibkr_etl.specialist_evidence import RegisteredEvidenceReader
from ibkr_etl.agent_daily_worker import run_daily_cycle


def main():
    target = os.environ['SPECIALIST_REPORT_DATABASE_URL']
    validate_database_url(target)
    def connect():
        return psycopg.connect(target, autocommit=True, connect_timeout=15, row_factory=dict_row)
    replay = globals().get('REPLAY_DATE')
    replay = date.fromisoformat(replay) if replay is not None else None
    result = run_daily_cycle(connect, RegisteredEvidenceReader(managed_reporting_archive()),
                             replay_date=replay, emit=lambda line: print(line, flush=True))
    print('SPECIALIST_DAILY_CYCLE ' + json.dumps(result, sort_keys=True), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # No exception text can expose database URLs, source values or secrets.
        print('SPECIALIST_DAILY_CYCLE ' + json.dumps(dict(status='failed', error_type=type(error).__name__,
            source_rows_changed=0, email_submitted=False)), flush=True)
        raise SystemExit(1)
