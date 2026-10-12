"""Narrow, audited retirement of the superseded disclaimer registration only."""
import hashlib,json,os
from datetime import datetime,timezone
from urllib.parse import urlsplit
import psycopg
from psycopg.rows import dict_row

TENANT='00000000-0000-0000-0000-000000000001'
OPERATION='ms-september-disclaimer-duplicate-20261011-v1'
SELECT='''SELECT a.*,s.subject_id,s.period_end,s.snapshot_id,s.status AS snapshot_status,s.quality_status,
    r.report_type,r.status AS generation_status,d.status AS decision_status,d.release_scope AS decision_scope,
    d.snapshot_id AS decision_snapshot FROM report_artifact_releases a
    JOIN report_generation_runs r ON r.tenant_id=a.tenant_id AND r.generation_run_id=a.generation_run_id
    JOIN reporting_snapshots s ON s.tenant_id=r.tenant_id AND s.snapshot_id=r.snapshot_id
    JOIN report_release_decisions d ON d.tenant_id=a.tenant_id AND d.release_decision_id=a.release_decision_id
    WHERE a.tenant_id=%s AND a.artifact_release_id IN (55,57) ORDER BY a.artifact_release_id'''

def normalized(rows):return json.loads(json.dumps(rows,default=str))

def inspect(db,*,lock=False):
    rows=normalized(db.execute(SELECT+(' FOR UPDATE OF a,s,r,d' if lock else ''),(TENANT,)).fetchall())
    if [r['artifact_release_id'] for r in rows]!=[55,57]:raise ValueError('Exact registration pair required')
    for row,expected in zip(rows,EXPECTED):
        for key in ('subject_id','period_end','report_type','artifact_release_id','generation_run_id','release_decision_id','artifact_sha256',
                    'released_at','container_name','blob_name','snapshot_id','snapshot_status','quality_status','generation_status',
                    'decision_status','decision_scope','decision_snapshot'):
            if row[key]!=expected[key]:raise ValueError('Approved registration provenance changed')
        if row['release_scope']!='internal_shareholder':raise ValueError('Only internal registration reconciliation')
    return rows

def main():
    if PHASE not in ('rehearse','apply') or os.getuid()!=10001:raise ValueError('Unsupported operation')
    uri=os.environ['DATABASE_ADMIN_URL'];parsed=urlsplit(uri)
    if parsed.hostname!='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com' or parsed.path!='/ibkr_staging':raise ValueError('Wrong database boundary')
    with psycopg.connect(uri,sslmode='verify-full',sslrootcert='/etc/ssl/certs/ca-certificates.crt',connect_timeout=15,row_factory=dict_row,
            options='-c statement_timeout=30000 -c lock_timeout=5000 -c app.tenant_id='+TENANT) as db:
        db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('report-release:'+TENANT+':alpha-world-ms:2026-09-30',))
        rows=inspect(db,lock=True);old,new=rows
        prior=db.execute("SELECT audit_event_id,details_json FROM audit_events WHERE tenant_id=%s AND event_type='duplicate_report_registration_withdrawn' AND details_json::jsonb->>'operation_id'=%s ORDER BY audit_event_id",(TENANT,OPERATION)).fetchall()
        if prior:
            if PHASE!='apply' or len(prior)!=1 or old['revoked_at'] is None or old['revoked_by']!=OPERATION or new['revoked_at'] is not None:raise ValueError('Unexpected earlier operation')
            db.rollback()
            print('MS_DUPLICATE_FINAL '+json.dumps({'passed':True,'phase':PHASE,'operation_id':OPERATION,'already_applied':True,'audit_event_id':prior[0]['audit_event_id'],'old_release':55,'current_release':57,'source_files_deleted':0,'approval_decisions_changed':0}),flush=True);return
        if old['revoked_at'] is not None or new['revoked_at'] is not None:raise ValueError('Download status changed')
        if PHASE=='apply' and rows!=EXPECTED_ROWS:raise ValueError('Rehearsed metadata changed')
        now=datetime.now(timezone.utc).isoformat()
        changed=db.execute('''UPDATE report_artifact_releases SET revoked_at=%s,revoked_by=%s,state_revision=state_revision+1
            WHERE tenant_id=%s AND artifact_release_id=55 AND state_revision=%s AND revoked_at IS NULL''',
            (now,OPERATION,TENANT,old['state_revision']))
        if changed.rowcount!=1:raise ValueError('Compare-and-set withdrawal failed')
        details={'operation_id':OPERATION,'old_registration_before':old,'retained_registration':new,
                 'reason':'Newer already-approved version contains the user-requested disclaimer; page 1 is pixel-identical.',
                 'review_receipt':REVIEW_RECEIPT,'blob_verification_receipt':BLOB_RECEIPT,'new_report_approval_granted':False}
        audit=db.execute('''INSERT INTO audit_events(tenant_id,occurred_at,actor_type,actor_id,event_type,subject_id,outcome,details_json)
            VALUES (%s,%s,'service',%s,'duplicate_report_registration_withdrawn','alpha-world-ms','internal_scope_client_blocked',%s)
            RETURNING audit_event_id''',(TENANT,now,OPERATION,json.dumps(details,sort_keys=True))).fetchone()
        after=inspect(db)
        expected_old={**old,'revoked_at':now,'revoked_by':OPERATION,'state_revision':old['state_revision']+1}
        if after!=[expected_old,new]:raise ValueError('Unexpected registration mutation')
        if PHASE=='rehearse':
            db.rollback()
            if inspect(db)!=rows or db.execute("SELECT 1 FROM audit_events WHERE tenant_id=%s AND event_type='duplicate_report_registration_withdrawn' AND details_json::jsonb->>'operation_id'=%s",(TENANT,OPERATION)).fetchone():raise ValueError('Record rollback failed')
            db.rollback()
        else:db.commit()
    print('MS_DUPLICATE_FINAL '+json.dumps({'passed':True,'phase':PHASE,'operation_id':OPERATION,'rows_before':rows,
        'audit_event_id':audit['audit_event_id'] if PHASE=='apply' else None,'rehearsal_rolled_back':PHASE=='rehearse',
        'registration_rows_changed':1 if PHASE=='apply' else 0,'old_release':55,'current_release':57,
        'source_files_deleted':0,'approval_decisions_changed':0,'sequence_gaps_possible':True}),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('MS_DUPLICATE_ERROR '+json.dumps({'type':type(e).__name__,'sqlstate':getattr(e,'sqlstate',None),'message':str(e) if isinstance(e,ValueError) else None}),flush=True)
        raise SystemExit(1)
