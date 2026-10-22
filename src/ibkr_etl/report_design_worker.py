"""Bound regeneration worker. New files remain private until owner review."""
from __future__ import annotations
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime,timedelta,timezone
from pathlib import Path

from .report_design_updates import decoded,digest,load_plan_inputs,qualified_design
from .util import canonical_json,utc_now_iso


def lease_until():
    return (datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat(timespec='seconds')


def verify_generated_pair(connection,tenant_id,plan,item):
    rows=connection.execute('''SELECT DISTINCT ON (report_type)
        generation_run_id,report_type,template_version,status,output_sha256,output_reference
        FROM report_generation_runs WHERE tenant_id=? AND snapshot_id=?
        AND report_type='factsheet' ORDER BY report_type,generation_run_id DESC''',
        (tenant_id,item['snapshot_id'])).fetchall()
    # Decks are no longer built: the regenerated package is the factsheet; an earlier deck stays as it was.
    result={}
    for r in rows:
        kind=r['report_type']
        if kind!='factsheet':continue
        expected=plan['template_versions'][kind]+'+design:'+plan['design_version']
        if (r['template_version']!=expected or r['status'] not in ('awaiting_approval','released_internal')
                or not re.fullmatch('[0-9a-f]{64}', str(r['output_sha256'] or ''))
                or not r['output_reference']):
            raise ValueError('Replacement pair is not fully validated under the selected design')
        result[kind]={'generation_run_id':r['generation_run_id'],'sha256':r['output_sha256']}
    if set(result)!={'factsheet'}:raise ValueError('Replacement factsheet is missing')
    return result


def worker_audit(connection, tenant_id, subject_id, request_id, event):
    connection.execute('''INSERT INTO audit_events(tenant_id,occurred_at,actor_type,
        actor_id,event_type,subject_id,outcome,details_json)
        VALUES (?,?,'system','report-design-worker',?,?,'source_preserved',?)''',
        (tenant_id,utc_now_iso(),event,subject_id,canonical_json({'request_id':str(request_id)})))


def finish_reviewed_updates(connection,tenant_id):
    """Only an entirely approved, still-current replacement batch activates future design."""
    rows=connection.execute('''SELECT * FROM report_design_update_requests
        WHERE tenant_id=? AND status='awaiting_review' FOR UPDATE''',(tenant_id,)).fetchall()
    for row in rows:
        plan=decoded(row['plan_json']);results=decoded(row['result_json'])
        if digest(plan)!=row['plan_sha256']:raise ValueError('Stored regeneration plan changed')
        if set(results)!={item['period_end'] for item in plan['snapshots']}:
            raise ValueError('Completed regeneration periods differ from the approved plan')
        for item in plan['snapshots']:
            if results[item['period_end']]!=verify_generated_pair(connection,tenant_id,plan,item):
                raise ValueError('Completed generation records changed before activation')
            frozen=connection.execute('''SELECT subject_id,period_end,data_sha256,status,quality_status
                FROM reporting_snapshots WHERE tenant_id=? AND snapshot_id=? FOR UPDATE''',
                (tenant_id,item['snapshot_id'])).fetchone()
            if (not frozen or frozen['subject_id']!=row['subject_id']
                    or str(frozen['period_end'])!=item['period_end']
                    or frozen['data_sha256']!=item['data_sha256']
                    or frozen['status'] not in ('frozen','approved') or frozen['quality_status']=='fail'):
                raise ValueError('Historical input changed before activation')
        account=connection.execute('SELECT lifecycle_status FROM platform_subjects WHERE tenant_id=? AND subject_id=? FOR UPDATE',
            (tenant_id,row['subject_id'])).fetchone()
        if not account or account['lifecycle_status']!='active':continue
        ids=[file['generation_run_id'] for pair in results.values() for file in pair.values()]
        if len(ids)!=len(plan['snapshots']) or len(set(ids))!=len(ids):
            raise ValueError('Completed regeneration evidence is incomplete')
        approved=connection.execute('''SELECT a.generation_run_id,a.artifact_sha256 FROM report_artifact_releases a
            JOIN report_release_decisions d ON d.tenant_id=a.tenant_id AND d.release_decision_id=a.release_decision_id
            JOIN report_generation_runs r ON r.tenant_id=a.tenant_id AND r.generation_run_id=a.generation_run_id
            JOIN reporting_snapshots s ON s.tenant_id=r.tenant_id AND s.snapshot_id=r.snapshot_id
            WHERE a.tenant_id=? AND s.subject_id=? AND a.generation_run_id=ANY(?)
            AND a.revoked_at IS NULL AND a.superseded_by_artifact_release_id IS NULL
            AND a.release_scope='internal_shareholder' AND d.status='approved'
            AND r.status='released_internal' AND s.status IN ('frozen','approved') AND s.quality_status<>'fail'
            FOR UPDATE OF a ''',
            (tenant_id,row['subject_id'],ids)).fetchall()
        expected={f['generation_run_id']:f['sha256'] for pair in results.values() for f in pair.values()}
        if {r['generation_run_id']:r['artifact_sha256'] for r in approved}!=expected:continue
        design=connection.execute('SELECT * FROM qualified_report_designs WHERE tenant_id=? AND subject_id=? AND design_version=?',
            (tenant_id,row['subject_id'],row['design_version'])).fetchone()
        qualified_design(design,installed_image_sha256=os.environ.get('REPORT_RENDERER_IMAGE_SHA256',''))
        now=utc_now_iso()
        connection.execute('''INSERT INTO active_report_designs(tenant_id,subject_id,design_version,request_id,activated_at)
            VALUES (?,?,?,?,?) ON CONFLICT (tenant_id,subject_id) DO UPDATE SET
            design_version=EXCLUDED.design_version,request_id=EXCLUDED.request_id,activated_at=EXCLUDED.activated_at''',
            (tenant_id,row['subject_id'],row['design_version'],row['request_id'],now))
        connection.execute('UPDATE report_design_update_requests SET status=\'completed\',updated_at=? WHERE tenant_id=? AND request_id=?',
            (now,tenant_id,row['request_id']))
        connection.execute('''INSERT INTO audit_events(tenant_id,occurred_at,actor_type,actor_id,event_type,
            subject_id,outcome,details_json) VALUES (?,?,'system','report-design-worker',
            'report_design_replacements_completed',?,'owner_approved_source_preserved',?)''',
            (tenant_id,now,row['subject_id'],canonical_json({'request_id':str(row['request_id']),'plan_sha256':row['plan_sha256']})))


def process_next_update(connection,tenant_id,project_root: Path,*,run=subprocess.run):
    if os.environ.get('REPORT_DESIGN_UPDATES_ENABLED')!='true':return {'status':'disabled'}
    finish_reviewed_updates(connection,tenant_id);connection.commit()
    now=utc_now_iso();token=str(uuid.uuid4())
    row=connection.execute('''SELECT * FROM report_design_update_requests WHERE tenant_id=?
        AND (status='queued' OR (status='running' AND lease_until<?))
        ORDER BY requested_at LIMIT 1 FOR UPDATE SKIP LOCKED''',(tenant_id,now)).fetchone()
    if row is None:connection.commit();return {'status':'idle'}
    subject=str(row['subject_id']);request=str(row['request_id'])
    connection.execute('''UPDATE report_design_update_requests SET status='running',lease_token=?,lease_until=?,updated_at=?
        WHERE tenant_id=? AND request_id=?''',(token,lease_until(),now,tenant_id,request))
    worker_audit(connection,tenant_id,subject,request,'report_design_regeneration_started')
    connection.commit()
    try:
        active=connection.execute('SELECT lifecycle_status FROM platform_subjects WHERE tenant_id=? AND subject_id=?',
            (tenant_id,subject)).fetchone()
        if not active or active['lifecycle_status']!='active':raise ValueError('Account is no longer active')
        plan=decoded(row['plan_json'])
        if digest(plan)!=row['plan_sha256']:raise ValueError('Regeneration plan integrity failed')
        current=load_plan_inputs(connection,tenant_id,subject,row['design_version'],
            installed_image_sha256=os.environ.get('REPORT_RENDERER_IMAGE_SHA256',''))
        if current!=plan:raise ValueError('Frozen inputs or qualified design changed after owner consent')
        results=decoded(row.get('result_json')) or {}
        connection.commit()
        for item in plan['snapshots']:
            claimed=connection.execute('''UPDATE report_design_update_requests SET lease_until=?,updated_at=?
                WHERE tenant_id=? AND request_id=? AND lease_token=? AND status='running' ''',
                (lease_until(),utc_now_iso(),tenant_id,request,token))
            if claimed.rowcount!=1:raise ValueError('Regeneration lease was lost')
            connection.commit()
            environment=dict(os.environ)
            environment.update({'PILOT_ACCOUNT_ID':subject,'REPORT_PERIOD_END':item['period_end'],
                'REPORT_DESIGN_VERSION':plan['design_version'],'REPORT_BOUND_SNAPSHOT_ID':item['snapshot_id'],
                'REPORT_BOUND_SNAPSHOT_SHA256':item['data_sha256']})
            # Do not allow another account's deployment-level factsheet override.
            if subject!='U16088571':
                environment.pop('FACTSHEET_TEMPLATE_BLOB',None);environment.pop('FACTSHEET_TEMPLATE_SHA256',None)
            run([sys.executable,str(project_root/'scripts/generate_month_end_package.py')],
                cwd=project_root,env=environment,check=True,timeout=1200,capture_output=True,text=True)
            results[item['period_end']]=verify_generated_pair(connection,tenant_id,plan,item)
            updated=connection.execute('''UPDATE report_design_update_requests SET result_json=?,updated_at=?
                WHERE tenant_id=? AND request_id=? AND lease_token=? AND status='running' ''',
                (canonical_json(results),utc_now_iso(),tenant_id,request,token))
            if updated.rowcount!=1:raise ValueError('Regeneration lease was lost')
            connection.commit()
        updated=connection.execute('''UPDATE report_design_update_requests SET status='awaiting_review',
            lease_token=NULL,lease_until=NULL,updated_at=? WHERE tenant_id=? AND request_id=? AND lease_token=?''',
            (utc_now_iso(),tenant_id,request,token))
        if updated.rowcount!=1:raise ValueError('Regeneration lease was lost')
        worker_audit(connection,tenant_id,subject,request,'report_design_replacements_awaiting_review')
        connection.commit()
        return {'status':'awaiting_review','request_id':request,'months':len(results),'reports_released':False}
    except Exception:
        connection.rollback()
        failed=connection.execute('''UPDATE report_design_update_requests SET status='failed',lease_token=NULL,
            lease_until=NULL,updated_at=? WHERE tenant_id=? AND request_id=? AND lease_token=?''',
            (utc_now_iso(),tenant_id,request,token))
        if failed.rowcount==1:worker_audit(connection,tenant_id,subject,request,'report_design_regeneration_failed')
        connection.commit()
        raise
