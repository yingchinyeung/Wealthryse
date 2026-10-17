"""Manual service-identity permission check only; no financial rows or agent enrollment."""
import json
import os
from ibkr_etl.specialist_observation_runtime import observation_database,TENANT
from ibkr_etl.specialist_observation_access import COLUMNS


def check_relations(c):
    """LIMIT 0 checks each granted column without reading a row. Names are unqualified, so the
    session's search_path finds a table moved to a layer schema rather than its view in public."""
    for table,columns in sorted(COLUMNS.items()):
        c.execute(f"SELECT {', '.join(columns.split())} FROM {table} LIMIT 0")
    return len(COLUMNS)


def main():
    with observation_database(os.environ['SPECIALIST_OBSERVATION_DATABASE_URL'],TENANT) as c:
        check_relations(c)
    print('SPECIALIST_OBSERVATION_WORKER '+json.dumps(dict(status='passed',database_access='verified',
        allowed_relation_checks=len(COLUMNS),financial_rows_read=0,source_rows_written=0,
        authority_rows_written=0,agents_enabled=0,messages_sent=0)),flush=True)


if __name__=='__main__':
    try: main()
    except Exception as error:
        print('SPECIALIST_OBSERVATION_WORKER '+json.dumps(dict(status='failed',error_type=type(error).__name__,
            reason=str(error) if isinstance(error,ValueError) else 'Observation access check failed')),flush=True)
        raise SystemExit(1)
