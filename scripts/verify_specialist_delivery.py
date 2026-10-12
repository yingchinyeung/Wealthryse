"""Rollback-only PostgreSQL delivery test; no email, website or source mutation."""
import json
import os
from datetime import date
import psycopg
from psycopg.rows import dict_row
from ibkr_etl.agent_daily_outbox import (
    enqueue_report, claim_delivery, start_send, finish_send, expire_ambiguous_sends)


def main():
    schema = 'specialist_delivery_trial'
    role = 'ibkr_specialist_delivery_trial'
    tenant = '00000000-0000-0000-0000-000000000001'
    other = '00000000-0000-0000-0000-000000000002'
    day = date(2026,10,1)
    checks = []
    with psycopg.connect(os.environ['DATABASE_URL'], autocommit=True, connect_timeout=20,
            options='-c statement_timeout=30000 -c lock_timeout=5000', row_factory=dict_row) as c:
        def absent():
            return not c.execute('SELECT 1 FROM pg_namespace WHERE nspname=%s',(schema,)).fetchone() and not c.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(role,)).fetchone()
        if not absent(): raise ValueError('Existing trial objects; refusing changes')
        def check(name, condition):
            if not condition: raise ValueError('Failed check: '+name)
            checks.append(name)
        def reject(name, call, error=ValueError):
            try:
                with c.transaction(): call()
            except error: checks.append(name)
            else: raise ValueError('Missing rejection: '+name)
        with c.transaction(force_rollback=True):
            c.execute('CREATE SCHEMA specialist_delivery_trial')
            c.execute('SET LOCAL search_path TO specialist_delivery_trial,public')
            c.execute('CREATE TABLE tenants(tenant_id UUID PRIMARY KEY)')
            c.execute('INSERT INTO tenants VALUES (%s),(%s)',(tenant,other))
            c.execute("CREATE FUNCTION app_tenant_id() RETURNS uuid LANGUAGE sql AS $$ SELECT current_setting('app.tenant_id')::uuid $$")
            c.execute(MIGRATION_SQL)
            c.execute('CREATE ROLE ibkr_specialist_delivery_trial NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS')
            c.execute('GRANT ibkr_specialist_delivery_trial TO CURRENT_USER')
            c.execute('GRANT USAGE ON SCHEMA specialist_delivery_trial TO ibkr_specialist_delivery_trial')
            c.execute('GRANT SELECT,INSERT,UPDATE,DELETE,TRUNCATE ON specialist_daily_reports,specialist_report_deliveries TO ibkr_specialist_delivery_trial')
            c.execute('GRANT EXECUTE ON FUNCTION app_tenant_id(),guard_specialist_daily_report() TO ibkr_specialist_delivery_trial')
            c.execute('SET LOCAL ROLE ibkr_specialist_delivery_trial')
            c.execute("SELECT set_config('app.tenant_id',%s,true)",(tenant,))
            fields = dict(tenant_id=tenant, report_date=day, report={'synthetic':True,'all_system_actions_captured':False})
            first = enqueue_report(c,**fields)
            check('idempotent_report_retry',enqueue_report(c,**fields)==first)
            check('exactly_two_channel_rows',c.execute('SELECT count(*) AS n FROM specialist_report_deliveries').fetchone()['n']==2)
            reject('conflicting_report_rejected',lambda:enqueue_report(c,**dict(fields,report={'changed':True})))
            for verb in ('UPDATE specialist_daily_reports SET report_date=report_date','DELETE FROM specialist_daily_reports','TRUNCATE specialist_daily_reports CASCADE'):
                reject('report_'+verb.split()[0].lower()+'_denied',lambda sql=verb:c.execute(sql),psycopg.errors.RaiseException)
            lease = claim_delivery(c,tenant_id=tenant,channel='email')
            check('lease_obtained',lease['state']=='leased' and lease['attempts']==1)
            check('second_claim_excluded',claim_delivery(c,tenant_id=tenant,channel='email') is None)
            c.execute("UPDATE specialist_report_deliveries SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE channel='email'")
            fresh = claim_delivery(c,tenant_id=tenant,channel='email')
            check('expired_presend_lease_reclaimed',fresh['lease_token']!=lease['lease_token'] and fresh['attempts']==2)
            common = dict(tenant_id=tenant,report_date=day,channel='email')
            reject('stale_worker_cannot_send',lambda:start_send(c,**common,lease_token=lease['lease_token']))
            start_send(c,**common,lease_token=fresh['lease_token'])
            c.execute("UPDATE specialist_report_deliveries SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE channel='email'")
            check('sending_not_blindly_retried',claim_delivery(c,tenant_id=tenant,channel='email') is None)
            check('missing_ack_becomes_uncertain',len(expire_ambiguous_sends(c,tenant_id=tenant))==1)
            check('uncertain_send_not_reclaimed',claim_delivery(c,tenant_id=tenant,channel='email') is None)
            reject('late_worker_cannot_overwrite_outcome',lambda:finish_send(c,**common,lease_token=fresh['lease_token'],outcome='accepted',provider_reference='synthetic-provider'))
            website = claim_delivery(c,tenant_id=tenant,channel='website')
            check('website_independent_of_email',website is not None)
            web = dict(tenant_id=tenant,report_date=day,channel='website',lease_token=website['lease_token'])
            start_send(c,**web)
            reject('success_requires_provider_evidence',lambda:finish_send(c,**web,outcome='delivered'))
            finish_send(c,**web,outcome='delivered',provider_reference='synthetic-inbox-receipt')
            check('completed_channel_not_reclaimed',claim_delivery(c,tenant_id=tenant,channel='website') is None)
            future_day = date(2099,1,1)
            enqueue_report(c,**dict(fields,report_date=future_day))
            check('future_report_not_sent_early',claim_delivery(c,tenant_id=tenant,channel='email') is None)
            c.execute("UPDATE specialist_report_deliveries SET available_at=clock_timestamp()-interval '1 second',attempts=8 WHERE report_date=%s AND channel='email'",(future_day,))
            check('bounded_lease_attempts',claim_delivery(c,tenant_id=tenant,channel='email') is None and c.execute("SELECT state FROM specialist_report_deliveries WHERE report_date=%s AND channel='email'",(future_day,)).fetchone()['state']=='failed')
            reject('half_cleared_lease_rejected',lambda:c.execute("UPDATE specialist_report_deliveries SET lease_token='00000000-0000-0000-0000-000000000010' WHERE report_date=%s AND channel='email'",(future_day,)),psycopg.errors.CheckViolation)
            c.execute("SELECT set_config('app.tenant_id',%s,true)",(other,))
            check('cross_tenant_hidden',c.execute('SELECT count(*) AS n FROM specialist_daily_reports').fetchone()['n']==0)
            reject('cross_tenant_worker_denied',lambda:claim_delivery(c,tenant_id=tenant,channel='email'))
        check('all_trial_objects_rolled_back',absent())
    print('SPECIALIST_DELIVERY_TRIAL '+json.dumps({'status':'passed','checks':checks,'checks_passed':len(checks),
        'persistent_changes':False,'messages_sent':0,'mode':'isolated_schema_force_rollback'}),flush=True)


if __name__=='__main__':
    try: main()
    except Exception as error:
        print('SPECIALIST_DELIVERY_TRIAL '+json.dumps({'status':'error','error_type':type(error).__name__,
            'reason':str(error) if isinstance(error,ValueError) else 'Database/runtime check failed'}),flush=True)
        raise SystemExit(1)
