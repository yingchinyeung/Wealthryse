"""Roll back temporary column grants after metadata-only permission rehearsal.

No financial rows, credentials, raw statements, or authority records are read.
The existing source tables, policies and views are never edited.
"""
import json
import os
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from ibkr_etl.specialist_observation_access import TRIAL_ROLE,COLUMNS,DENIED_COLUMNS,grant_statements,layer_grant_statements,verify_permissions

TENANT='00000000-0000-0000-0000-000000000001'


def main():
    if os.environ.get('PORTFOLIO_TENANT_ID')!=TENANT: raise ValueError('Unexpected observation tenant')
    with psycopg.connect(os.environ['DATABASE_URL'],autocommit=True,connect_timeout=20,row_factory=dict_row,
        options='-c statement_timeout=30000 -c lock_timeout=2000') as c:
        if c.info.dbname!='ibkr_staging' or c.info.host!='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com':
            raise ValueError('Unexpected observation database')
        exists=lambda:c.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(TRIAL_ROLE,)).fetchone()
        if exists(): raise ValueError('Observation trial role already exists')
        with c.transaction(force_rollback=True):
            c.execute('CREATE ROLE ibkr_specialist_observation_trial NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS')
            for statement in grant_statements(TRIAL_ROLE)+layer_grant_statements(c,TRIAL_ROLE): c.execute(statement)
            c.execute('GRANT ibkr_specialist_observation_trial TO CURRENT_USER WITH ADMIN FALSE, INHERIT FALSE, SET TRUE')
            c.execute('SET LOCAL ROLE ibkr_specialist_observation_trial')
            c.execute("SELECT set_config('app.tenant_id',%s,true)",(TENANT,))
            evidence=verify_permissions(c,TRIAL_ROLE)
            # LIMIT 0 parses and checks permissions without retrieving a row.
            allowed=0
            for table,columns in sorted(COLUMNS.items()):
                c.execute(sql.SQL('SELECT {} FROM public.{} LIMIT 0').format(
                    sql.SQL(',').join(map(sql.Identifier,columns.split())),sql.Identifier(table)))
                allowed+=1
            denied=0
            for table,column in DENIED_COLUMNS:
                try:
                    with c.transaction():
                        c.execute(sql.SQL('SELECT {} FROM public.{} LIMIT 0').format(sql.Identifier(column),sql.Identifier(table)))
                except psycopg.errors.InsufficientPrivilege: denied+=1
                else: raise ValueError('Forbidden observation column accessible')
            c.execute('RESET ROLE')
        if exists(): raise ValueError('Observation trial role rollback not confirmed')
    print('SPECIALIST_OBSERVATION_READER_TRIAL '+json.dumps(dict(status='passed',**evidence,
        allowed_relation_checks=allowed,denied_column_checks=denied,financial_rows_read=0,
        source_rows_written=0,trial_role_rolled_back=True,service_login_created=False,agents_enabled=0)),flush=True)


if __name__=='__main__':
    try: main()
    except Exception as error:
        print('SPECIALIST_OBSERVATION_READER_TRIAL '+json.dumps(dict(status='failed',error_type=type(error).__name__,
            reason=str(error) if isinstance(error,ValueError) else 'Observation reader trial failed')),flush=True)
        raise SystemExit(1)
