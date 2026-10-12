"""Isolated, committed synthetic schema exercise followed by explicit cleanup.

Unlike rollback-only tests, this verifies real report commits. No production
table writes, protected archive writes, mail, or scheduler changes occur. The
archive adapter is a synthetic exact-byte simulator, not live archive proof.
"""
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
import psycopg
from psycopg.rows import dict_row
from ibkr_etl.agent_daily_report import prepare_daily_report, render_daily_message
from ibkr_etl.agent_daily_delivery import dispatch_daily_email
from ibkr_etl.agent_daily_inbox import publish_website_report, list_daily_reports, get_daily_report
from ibkr_etl.specialist_service_access import ROLES as SERVICE_ROLES, grant_statements
from psycopg import sql
from ibkr_etl.agent_daily_schedule import report_window
from ibkr_etl.util import canonical_json

ROLES={capability:role+'_trial' for capability,role in SERVICE_ROLES.items()}


def main():
    schema='specialist_report_builder_trial'
    tenant='00000000-0000-0000-0000-000000000001'
    day=datetime.now(timezone.utc).date()-timedelta(days=2)
    start,end=report_window(day)
    checks=[]
    retained={}
    class Archive:
        def check_ready(self):
            return {'protected_archive_verified':True,'mode':'synthetic_only'}
        def verify(self, tenant_id, event):
            if tenant_id != tenant or retained.get(event['sequence']) != canonical_json(event):
                raise ValueError('Synthetic archive mismatch')
        def verify_detail(self, tenant_id, event):
            # Isolated synthetic adapter only; live evidence uses the reviewed registry.
            self.verify(tenant_id,event)
            return {'detail_verified':True,'evidence_sha256':event['evidence_sha256'],'mode':'synthetic_only'}
    with psycopg.connect(os.environ['DATABASE_URL'],autocommit=True,connect_timeout=20,
            options='-c statement_timeout=30000 -c lock_timeout=5000',row_factory=dict_row) as c:
        def check(name, condition):
            if not condition: raise ValueError('Failed check: '+name)
            checks.append(name)
        def absent():
            return not c.execute('SELECT 1 FROM pg_namespace WHERE nspname=%s',(schema,)).fetchone() and not c.execute('SELECT 1 FROM pg_roles WHERE rolname=ANY(%s)',(list(ROLES.values()),)).fetchone()
        if not absent(): raise ValueError('Existing trial objects; refusing changes')
        created=False
        try:
            with c.transaction():
                c.execute('CREATE SCHEMA specialist_report_builder_trial')
                c.execute('REVOKE ALL ON SCHEMA specialist_report_builder_trial FROM PUBLIC')
                c.execute('SET LOCAL search_path TO specialist_report_builder_trial,public')
                c.execute('CREATE TABLE tenants(tenant_id UUID PRIMARY KEY)')
                c.execute('INSERT INTO tenants VALUES (%s)',(tenant,))
                c.execute("CREATE FUNCTION app_tenant_id() RETURNS uuid LANGUAGE sql AS $$ SELECT current_setting('app.tenant_id')::uuid $$")
                c.execute(MIGRATION_SQL)
                for capability,role in ROLES.items():
                    c.execute(sql.SQL('CREATE ROLE {} NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS').format(sql.Identifier(role)))
                    c.execute(sql.SQL('GRANT {} TO CURRENT_USER').format(sql.Identifier(role)))
                    for statement in grant_statements(capability,schema=schema): c.execute(statement)
            created=True
            c.execute('SET search_path TO specialist_report_builder_trial,public')
            c.execute('SET ROLE ibkr_specialist_audit_writer_trial')
            c.execute("SELECT set_config('app.tenant_id',%s,false)",(tenant,))
            previous='0'*64
            for seq,at in enumerate((start-timedelta(seconds=1),start,end),1):
                e=dict(sequence=seq,event_id=f'synthetic-{seq}',occurred_at=at.isoformat(),role='platform_operations',
                       action='inspect_and_analyze',operation_id='synthetic-operation',phase='succeeded',
                       evidence_sha256='a'*64,cost_microusd=0,previous_sha256=previous)
                e['sha256']=hashlib.sha256(canonical_json(e).encode()).hexdigest()
                c.execute('''INSERT INTO specialist_audit_events
                    (tenant_id,sequence_no,event_id,event_json,previous_sha256,event_sha256)
                    VALUES (%s,%s,%s,%s::jsonb,%s,%s)''',(tenant,seq,e['event_id'],canonical_json(e),previous,e['sha256']))
                retained[seq]=canonical_json(e); previous=e['sha256']
            c.execute('RESET ROLE')
            c.execute('SET ROLE ibkr_specialist_report_worker_trial')
            saved=retained.pop(2)
            try: prepare_daily_report(c,Archive(),tenant_id=tenant,report_date=day)
            except ValueError: checks.append('missing_archive_rejected')
            else: raise ValueError('Missing archive was accepted')
            check('failed_verification_not_published',c.execute('SELECT count(*) AS n FROM specialist_daily_reports').fetchone()['n']==0)
            retained[2]=saved
            first=prepare_daily_report(c,Archive(),tenant_id=tenant,report_date=day)
            check('real_report_commit',first['database_committed'] and c.info.transaction_status==0)
            check('start_included_end_excluded',first['event_count']==1)
            check('repeat_is_idempotent',prepare_daily_report(c,Archive(),tenant_id=tenant,report_date=day)['report_sha256']==first['report_sha256'])
            check('two_delivery_channels',c.execute('SELECT count(*) AS n FROM specialist_report_deliveries').fetchone()['n']==2)
            content=c.execute('SELECT report_json FROM specialist_daily_reports WHERE report_date=%s',(day,)).fetchone()['report_json']
            message=render_daily_message(content)
            check('owner_recipient_fixed',message['recipient']=='anthony.ying@wealthryse.ai')
            check('coverage_limit_disclosed','complete system-wide capture is not yet established' in message['text'])
            no_activity=prepare_daily_report(c,Archive(),tenant_id=tenant,report_date=day-timedelta(days=2))
            check('no_activity_report_created',no_activity['event_count']==0)
            check('no_send_claimed',first['messages_sent']==0 and no_activity['messages_sent']==0)
            class Sender:
                calls=0
                def submit(self,message,**kwargs):
                    self.calls+=1
                    check('send_follows_committed_state_'+str(self.calls),c.info.transaction_status==0 and
                          c.execute("SELECT count(*) AS n FROM specialist_report_deliveries WHERE state='sending'").fetchone()['n']==1)
                    if self.calls==2: raise TimeoutError('Synthetic acknowledgement loss')
                    return {'state':'accepted','provider_reference':'synthetic-provider-ack'}
            sender=Sender()
            accepted=dispatch_daily_email(c,sender,tenant_id=tenant)
            check('provider_acceptance_not_delivery',accepted['state']=='accepted' and not accepted['recipient_delivery_confirmed'])
            uncertain=dispatch_daily_email(c,sender,tenant_id=tenant)
            check('provider_timeout_retained_as_uncertain',uncertain['state']=='uncertain')
            check('ambiguous_email_not_retried',dispatch_daily_email(c,sender,tenant_id=tenant)['state']=='idle' and sender.calls==2)
            check('website_queue_independent',c.execute("SELECT count(*) AS n FROM specialist_report_deliveries WHERE channel='website' AND state='pending'").fetchone()['n']==2)
            check('unpublished_reports_hidden',list_daily_reports(c,tenant_id=tenant)==[])
            for _ in range(2):
                publication=publish_website_report(c,tenant_id=tenant)
                check('website_published_'+publication['report_date'],publication['state']=='available_on_website' and not publication['owner_read_confirmed'])
            check('website_publication_idempotent',publish_website_report(c,tenant_id=tenant)['state']=='idle')
            c.execute('RESET ROLE')
            c.execute('SET ROLE ibkr_specialist_report_reader_trial')
            check('reader_lists_two_published_reports',len(list_daily_reports(c,tenant_id=tenant))==2)
            check('reader_verifies_report_content',get_daily_report(c,tenant_id=tenant,report_date=day)['report_sha256']==first['report_sha256'])
            c.execute("SELECT set_config('app.tenant_id',%s,false)",('00000000-0000-0000-0000-000000000002',))
            check('reader_cross_tenant_isolation',list_daily_reports(c,tenant_id='00000000-0000-0000-0000-000000000002')==[])
            c.execute("SELECT set_config('app.tenant_id',%s,false)",(tenant,))
            def denied(name, statement):
                try: c.execute(statement)
                except psycopg.errors.InsufficientPrivilege: checks.append(name)
                else: raise ValueError('Unexpected permission: '+name)
            denied('reader_cannot_update_delivery',"UPDATE specialist_report_deliveries SET state='failed' WHERE false")
            denied('reader_cannot_insert_report',"INSERT INTO specialist_daily_reports SELECT * FROM specialist_daily_reports WHERE false")
            denied('reader_cannot_read_audit',"SELECT * FROM specialist_audit_events LIMIT 0")
            c.execute('RESET ROLE'); c.execute('SET ROLE ibkr_specialist_report_worker_trial')
            denied('worker_cannot_rewrite_report',"UPDATE specialist_daily_reports SET report_sha256=report_sha256 WHERE false")
            denied('worker_cannot_change_delivery_identity',"UPDATE specialist_report_deliveries SET report_date=report_date WHERE false")
            denied('worker_cannot_insert_audit',"INSERT INTO specialist_audit_events SELECT * FROM specialist_audit_events WHERE false")
            c.execute('RESET ROLE'); c.execute('SET ROLE ibkr_specialist_audit_writer_trial')
            denied('audit_writer_cannot_read_reports',"SELECT * FROM specialist_daily_reports LIMIT 0")
            denied('audit_writer_cannot_rewrite_audit',"UPDATE specialist_audit_events SET event_json=event_json WHERE false")
            for capability,role in ROLES.items():
                c.execute('RESET ROLE')
                c.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(role)))
                check(capability+'_no_production_table_write_privileges',c.execute("""SELECT count(*) AS n FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace
                WHERE n.nspname='public' AND t.relkind IN ('r','p','v','m') AND
                (has_table_privilege(current_user,t.oid,'INSERT') OR has_table_privilege(current_user,t.oid,'UPDATE')
                OR has_table_privilege(current_user,t.oid,'DELETE') OR has_table_privilege(current_user,t.oid,'TRUNCATE'))""").fetchone()['n']==0)
        finally:
            c.execute('RESET ROLE')
            c.execute('RESET search_path')
            if created:
                # Exact private trial schema/role created above, never a computed
                # schema supplied by an agent or a pre-existing application target.
                with c.transaction():
                    c.execute('DROP SCHEMA specialist_report_builder_trial CASCADE')
                    for role in ROLES.values(): c.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
        check('isolated_trial_objects_removed',absent())
    print('SPECIALIST_REPORT_BUILDER_TRIAL '+json.dumps({'status':'passed','checks':checks,'checks_passed':len(checks),
        'persistent_changes':False,'messages_sent':0,'archive':'synthetic_exact_byte_simulator',
        'mode':'isolated_committed_schema_then_cleanup'}),flush=True)


if __name__=='__main__':
    try: main()
    except Exception as error:
        print('SPECIALIST_REPORT_BUILDER_TRIAL '+json.dumps({'status':'error','error_type':type(error).__name__,
            'reason':str(error) if isinstance(error,ValueError) else 'Database/runtime check failed'}),flush=True)
        raise SystemExit(1)
