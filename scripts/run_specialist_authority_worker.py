"""Manual authority-reader acceptance only; does not enroll or enable agents."""
import json
import os
from ibkr_etl.specialist_authority_runtime import authority_database,TENANT
from ibkr_etl.agent_authority import PostgresAuthoritySource


def main():
    value=os.environ['SPECIALIST_AUTHORITY_DATABASE_URL']
    factory=lambda tenant:authority_database(value,tenant)
    with factory(TENANT) as c:
        counts=c.execute('''SELECT (SELECT count(*) FROM specialist_observation_controls) AS controls,
            (SELECT count(*) FROM specialist_observation_grants) AS grants''').fetchone()
        if (set(counts)!={'controls','grants'} or any(type(counts[k]) is not int or counts[k]<0 for k in counts)
            or counts['controls']>1 or (counts['grants']>0 and counts['controls']!=1)):
            raise ValueError('Invalid tenant authority store shape')
    # A syntactically valid unregistered principal; never inserted into the database.
    state=PostgresAuthoritySource(factory).load_current(tenant_id=TENANT,
        principal_id='00000000-0000-0000-0000-000000000003')
    if state.grant is not None or state.kill_switch is not True:
        raise ValueError('Unregistered principal was not denied')
    print('SPECIALIST_AUTHORITY_WORKER '+json.dumps(dict(status='passed',database_access='verified',
        authority_source_checked=True,unregistered_principal_denied=True,tenant_authority_rows=dict(counts),
        source_table_access=False,authority_rows_written=0,agents_enabled=0,messages_sent=0)),flush=True)


if __name__=='__main__':
    try: main()
    except Exception as error:
        print('SPECIALIST_AUTHORITY_WORKER '+json.dumps(dict(status='failed',error_type=type(error).__name__,
            reason=str(error) if isinstance(error,ValueError) else 'Authority reader check failed')),flush=True)
        raise SystemExit(1)
