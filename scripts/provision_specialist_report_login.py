"""Add one login bound to a pre-existing capability; quarantine on failed checks."""
import json
import os
from urllib.parse import unquote
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from ibkr_etl.specialist_report_runtime import validate_database_url, bind_report_session, verify_report_access, LOGIN, HOST, DATABASE


def main():
    target=validate_database_url(os.environ['SPECIALIST_REPORT_DATABASE_URL'])
    created=False
    with psycopg.connect(os.environ['DATABASE_URL'],autocommit=True,connect_timeout=20,row_factory=dict_row,
                        options='-c statement_timeout=60000 -c lock_timeout=5000') as admin:
        if admin.info.host!=HOST or admin.info.dbname!=DATABASE: raise ValueError('Unexpected administrator target')
        try:
            with admin.transaction():
                if admin.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(LOGIN,)).fetchone():
                    raise ValueError('Reporting login already exists; refusing implicit alteration')
                admin.execute(sql.SQL('CREATE ROLE ibkr_specialist_report_login LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 2 PASSWORD {}').format(sql.Literal(unquote(target.password))))
                admin.execute('GRANT CONNECT ON DATABASE ibkr_staging TO ibkr_specialist_report_login')
                admin.execute('GRANT ibkr_specialist_report_worker TO ibkr_specialist_report_login WITH ADMIN FALSE, INHERIT FALSE, SET TRUE')
                admin.execute("ALTER ROLE ibkr_specialist_report_login SET statement_timeout='60s'")
                admin.execute("ALTER ROLE ibkr_specialist_report_login SET idle_in_transaction_session_timeout='60s'")
            created=True
            # Actual fresh login, not SET ROLE under an administrator session.
            with psycopg.connect(os.environ['SPECIALIST_REPORT_DATABASE_URL'],autocommit=True,connect_timeout=20,row_factory=dict_row) as c:
                bind_report_session(c)
                result=verify_report_access(c)
            print('SPECIALIST_REPORT_PROVISION '+json.dumps(dict(result,status='passed',login_created=True)),flush=True)
        except Exception:
            if created:
                admin.execute('ALTER ROLE ibkr_specialist_report_login NOLOGIN')
            raise


if __name__=='__main__':
    try: main()
    except Exception as error:
        print('SPECIALIST_REPORT_PROVISION '+json.dumps({'status':'failed','error_type':type(error).__name__,
              'reason':str(error) if isinstance(error,ValueError) else 'Restricted login provisioning failed'}),flush=True)
        raise SystemExit(1)
