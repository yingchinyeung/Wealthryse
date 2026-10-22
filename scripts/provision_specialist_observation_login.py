"""Create the rehearsed reader capability and one restricted login, then authenticate."""
import json
import os
from urllib.parse import unquote
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from ibkr_etl.specialist_observation_access import ROLE,grant_statements,layer_grant_statements,verify_permissions
from ibkr_etl.specialist_observation_runtime import LOGIN,HOST,DATABASE,validate_database_url,bind_observation_session,verify_observation_access


def verify_creator_membership(c,role):
    # PostgreSQL creates this ADMIN-only lifecycle link itself. Do not try to
    # re-grant ADMIN to the grantor or turn it into executable membership.
    rows=c.execute('''SELECT m.admin_option,m.inherit_option,m.set_option
        FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.roleid
        JOIN pg_roles member ON member.oid=m.member
        WHERE r.rolname=%s AND member.rolname=current_user''',(role,)).fetchall()
    if rows!=[dict(admin_option=True,inherit_option=False,set_option=False)]:
        raise ValueError('Creator lifecycle membership is not ADMIN-only')


def main():
    target=validate_database_url(os.environ['SPECIALIST_OBSERVATION_DATABASE_URL'])
    created=False
    with psycopg.connect(os.environ['DATABASE_URL'],autocommit=True,connect_timeout=20,row_factory=dict_row,
        options='-c statement_timeout=60000 -c lock_timeout=5000') as admin:
        if admin.info.host!=HOST or admin.info.dbname!=DATABASE: raise ValueError('Unexpected administrator target')
        try:
            with admin.transaction():
                if admin.execute('SELECT 1 FROM pg_roles WHERE rolname=ANY(%s)',([LOGIN,ROLE],)).fetchone():
                    raise ValueError('Observation roles already exist; refusing implicit alteration')
                admin.execute('CREATE ROLE ibkr_specialist_observation_reader NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS')
                for statement in grant_statements(ROLE)+layer_grant_statements(admin,ROLE): admin.execute(statement)
                verify_creator_membership(admin,ROLE)
                verify_permissions(admin,ROLE)
                admin.execute(sql.SQL('CREATE ROLE ibkr_specialist_observation_login LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 2 PASSWORD {}').format(sql.Literal(unquote(target.password))))
                verify_creator_membership(admin,LOGIN)
                admin.execute('GRANT CONNECT ON DATABASE ibkr_staging TO ibkr_specialist_observation_login')
                admin.execute('GRANT ibkr_specialist_observation_reader TO ibkr_specialist_observation_login WITH ADMIN FALSE, INHERIT FALSE, SET TRUE')
                admin.execute("ALTER ROLE ibkr_specialist_observation_login SET statement_timeout='60s'")
                admin.execute("ALTER ROLE ibkr_specialist_observation_login SET idle_in_transaction_session_timeout='60s'")
            created=True
            with psycopg.connect(os.environ['SPECIALIST_OBSERVATION_DATABASE_URL'],autocommit=True,connect_timeout=20,row_factory=dict_row) as c:
                with c.transaction():
                    bind_observation_session(c)
                    result=verify_observation_access(c)
            print('SPECIALIST_OBSERVATION_PROVISION '+json.dumps(dict(result,status='passed',login_created=True,financial_rows_read=0)),flush=True)
        except Exception:
            if created: admin.execute('ALTER ROLE ibkr_specialist_observation_login NOLOGIN')
            raise


if __name__=='__main__':
    try: main()
    except Exception as error:
        print('SPECIALIST_OBSERVATION_PROVISION '+json.dumps(dict(status='failed',error_type=type(error).__name__,
            reason=str(error) if isinstance(error,ValueError) else 'Observation provisioning failed; inspect before retry')),flush=True)
        raise SystemExit(1)
