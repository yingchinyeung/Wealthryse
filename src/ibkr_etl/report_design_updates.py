"""Owner requests, qualified-version binding and immutable regeneration plans.

No API registers executable code or declares a renderer qualified. The release
service must register an exact installed image and its independent gate evidence.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid

from .monthly_reports import validate_period_end
from .util import canonical_json, utc_now_iso

GATES=('regression','vulnerability','runtime','rollback','historical_visual','historical_financial')


def digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def decoded(value):
    return json.loads(value) if isinstance(value,str) else value


def qualified_design(row, *, installed_image_sha256):
    if not row or row.get('retired_at'):
        raise ValueError('The selected design is not currently qualified')
    if not re.fullmatch('[0-9a-f]{64}',installed_image_sha256 or ''):
        raise ValueError('The installed reporting image has not been bound')
    if row['runtime_image_sha256']!=installed_image_sha256:
        raise ValueError('The selected design requires a different reporting image')
    evidence=decoded(row['qualification_json'])
    if any(evidence.get(gate)!='passed' for gate in GATES):
        raise ValueError('The selected design has incomplete release qualification')
    if evidence.get('runtime_image_sha256')!=installed_image_sha256:
        raise ValueError('Qualification evidence belongs to another image')
    if not re.fullmatch('[0-9a-f]{64}',str(evidence.get('evidence_sha256') or '')):
        raise ValueError('The protected qualification receipt is missing')
    versions=decoded(row['template_versions_json'])
    if set(versions)!={'factsheet','presentation'} or any(not isinstance(v,str) or not v.strip() for v in versions.values()):
        raise ValueError('Both qualified template versions are required')
    return versions


def register_design(connection,tenant_id,subject_id,design_version,receipt_bytes,*,installed_image_sha256,actor):
    """Release-service only: create-only registration from a protected gate receipt.

    The operator must supply a receipt produced by the qualification pipeline.
    This function is deliberately not exposed through the website.
    """
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',design_version):
        raise ValueError('Design version must be a stable release identifier')
    receipt=json.loads(receipt_bytes)
    if receipt.get('subject_id')!=subject_id or receipt.get('design_version')!=design_version:
        raise ValueError('Qualification receipt belongs to another account or version')
    evidence=dict(receipt['qualification'])
    evidence['evidence_sha256']=hashlib.sha256(receipt_bytes).hexdigest()
    design={'subject_id':subject_id,'design_version':design_version,
        'runtime_image_sha256':receipt['runtime_image_sha256'],
        'template_versions_json':canonical_json(receipt['template_versions']),
        'qualification_json':canonical_json(evidence)}
    qualified_design(design,installed_image_sha256=installed_image_sha256)
    connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(?,0))',
        (f'report-design-registration:{tenant_id}:{subject_id}:{design_version}',))
    old=connection.execute('''SELECT * FROM qualified_report_designs
        WHERE tenant_id=? AND subject_id=? AND design_version=?''',(tenant_id,subject_id,design_version)).fetchone()
    if old:
        if old.get('retired_at') or any(old[key]!=value for key,value in design.items()):
            raise ValueError('A design version cannot be rebound or resurrected')
        return dict(old)
    row=connection.execute('''INSERT INTO qualified_report_designs(tenant_id,subject_id,design_version,
        runtime_image_sha256,template_versions_json,qualification_json,qualified_at)
        VALUES (?,?,?,?,?,?,?) RETURNING *''',
        (tenant_id,subject_id,design_version,design['runtime_image_sha256'],
         design['template_versions_json'],design['qualification_json'],utc_now_iso())).fetchone()
    audit(connection,tenant_id,subject_id,actor,'report_design_qualified_registered',
        {'design_version':design_version,'evidence_sha256':evidence['evidence_sha256'],
         'runtime_image_sha256':installed_image_sha256})
    return dict(row)


def build_plan(subject_id, design, snapshots, released_periods, *, installed_image_sha256):
    versions=qualified_design(design,installed_image_sha256=installed_image_sha256)
    if design['subject_id']!=subject_id:raise ValueError('Design belongs to another account')
    if not snapshots:raise ValueError('No frozen historical months are available')
    items=[];seen=set()
    for row in sorted(snapshots,key=lambda r:str(r['period_end'])):
        period=validate_period_end(str(row['period_end']))
        if period in seen:raise ValueError('More than one current snapshot exists for a month')
        seen.add(period)
        if row['subject_id']!=subject_id or row['status'] not in ('frozen','approved') or row['quality_status']=='fail':
            raise ValueError('A historical input is out of scope or fails quality')
        if not re.fullmatch('[0-9a-f]{64}',str(row['data_sha256'])):
            raise ValueError('Frozen input digest is invalid')
        uuid.UUID(str(row['snapshot_id']))
        items.append({'period_end':period,'snapshot_id':str(row['snapshot_id']),'data_sha256':row['data_sha256']})
    if not set(released_periods)<=seen:
        raise ValueError('A previously released month lacks a current frozen input')
    return {'version':'report-design-regeneration-v1','subject_id':subject_id,
        'design_version':design['design_version'],'runtime_image_sha256':installed_image_sha256,
        'template_versions':versions,'snapshots':items,'future_months':True,
        'source_policy':'unchanged_frozen_inputs','replacement_policy':'replace_on_owner_approval',
        'archive_policy':'preserve_prior_files_and_decisions','client_distribution':'prohibited'}


def load_plan_inputs(connection, tenant_id, subject_id, design_version, *, installed_image_sha256):
    design=connection.execute('''SELECT * FROM qualified_report_designs
        WHERE tenant_id=? AND subject_id=? AND design_version=?''',
        (tenant_id,subject_id,design_version)).fetchone()
    snapshots=connection.execute('''SELECT DISTINCT ON (period_end)
        snapshot_id,subject_id,period_end,status,quality_status,data_sha256
        FROM reporting_snapshots WHERE tenant_id=? AND subject_id=?
        AND status IN ('frozen','approved') ORDER BY period_end,created_at DESC,snapshot_id DESC''',
        (tenant_id,subject_id)).fetchall()
    released=connection.execute('''SELECT DISTINCT s.period_end FROM report_artifact_releases a
        JOIN report_generation_runs r ON r.tenant_id=a.tenant_id AND r.generation_run_id=a.generation_run_id
        JOIN reporting_snapshots s ON s.tenant_id=r.tenant_id AND s.snapshot_id=r.snapshot_id
        WHERE a.tenant_id=? AND s.subject_id=? AND a.release_scope='internal_shareholder' ''',
        (tenant_id,subject_id)).fetchall()
    return build_plan(subject_id,design,snapshots,[r['period_end'] for r in released],
        installed_image_sha256=installed_image_sha256)


def audit(connection,tenant_id,subject_id,actor,event,details):
    connection.execute('''INSERT INTO audit_events(tenant_id,occurred_at,actor_type,
        actor_id,event_type,subject_id,outcome,details_json)
        VALUES (?,?,'user',?,?,?,'source_preserved',?)''',
        (tenant_id,utc_now_iso(),actor,event,subject_id,canonical_json(details)))


def create_request(connection,tenant_id,subject_id,request_id,description,actor):
    request_id=str(uuid.UUID(str(request_id)));description=description.strip()
    if not 10<=len(description)<=4000:raise ValueError('Describe the design changes in 10 to 4,000 characters')
    connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(?,0))',
        (f'report-design-request:{tenant_id}:{request_id}',))
    old=connection.execute('SELECT * FROM report_design_update_requests WHERE tenant_id=? AND request_id=?',
        (tenant_id,request_id)).fetchone()
    if old:
        if old['subject_id']!=subject_id or old['description']!=description or old['requested_by']!=actor:
            raise ValueError('Request identifier is already bound to different input')
        return dict(old)
    now=utc_now_iso()
    row=connection.execute('''INSERT INTO report_design_update_requests(request_id,tenant_id,
        subject_id,requested_by,requested_at,description,status,updated_at)
        VALUES (?,?,?,?,?,?,'requested',?) RETURNING *''',
        (request_id,tenant_id,subject_id,actor,now,description,now)).fetchone()
    audit(connection,tenant_id,subject_id,actor,'report_design_change_requested',{'request_id':request_id})
    return dict(row)


def queue_request(connection,tenant_id,subject_id,request_id,design_version,expected_sha,actor,*,installed_image_sha256):
    request_id=str(uuid.UUID(str(request_id)))
    connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(?,0))',
        (f'report-design:{tenant_id}:{subject_id}',))
    row=connection.execute('''SELECT * FROM report_design_update_requests
        WHERE tenant_id=? AND subject_id=? AND request_id=? FOR UPDATE''',
        (tenant_id,subject_id,request_id)).fetchone()
    if row is None:raise ValueError('Design request not found')
    if row['status']!='requested':
        if row.get('plan_sha256')==expected_sha and row['design_version']==design_version:
            return dict(row)
        raise ValueError('This request has already changed state')
    busy=connection.execute('''SELECT request_id FROM report_design_update_requests
        WHERE tenant_id=? AND subject_id=? AND status IN ('queued','running','awaiting_review')''',
        (tenant_id,subject_id)).fetchone()
    if busy:raise ValueError('Finish the current design update before starting another')
    plan=load_plan_inputs(connection,tenant_id,subject_id,design_version,installed_image_sha256=installed_image_sha256)
    if digest(plan)!=expected_sha:raise ValueError('Design or frozen inputs changed; preview again')
    updated=connection.execute('''UPDATE report_design_update_requests SET status='queued',
        design_version=?,plan_json=?,plan_sha256=?,updated_at=?
        WHERE tenant_id=? AND subject_id=? AND request_id=? AND status='requested' RETURNING *''',
        (design_version,canonical_json(plan),expected_sha,utc_now_iso(),tenant_id,subject_id,request_id)).fetchone()
    if updated is None:raise ValueError('Request changed while queuing')
    audit(connection,tenant_id,subject_id,actor,'report_design_regeneration_queued',
        {'request_id':request_id,'plan_sha256':expected_sha,'months':len(plan['snapshots'])})
    return dict(updated)


def active_design(connection,tenant_id,subject_id,*,installed_image_sha256):
    row=connection.execute('''SELECT d.* FROM active_report_designs a
        JOIN qualified_report_designs d ON d.tenant_id=a.tenant_id AND d.subject_id=a.subject_id
        AND d.design_version=a.design_version WHERE a.tenant_id=? AND a.subject_id=?''',
        (tenant_id,subject_id)).fetchone()
    if row:qualified_design(row,installed_image_sha256=installed_image_sha256)
    return dict(row) if row else None


def supersede_prior_releases(connection,tenant_id,subject_id,period_end,report_type,new_id,actor,now):
    """Withdraw old links in the same approval transaction, never delete files."""
    return connection.execute('''UPDATE report_artifact_releases a
        SET revoked_at=?,revoked_by=?,state_revision=a.state_revision+1,
            superseded_by_artifact_release_id=?
        FROM report_generation_runs r,reporting_snapshots s
        WHERE a.tenant_id=? AND r.tenant_id=a.tenant_id AND s.tenant_id=r.tenant_id
          AND r.generation_run_id=a.generation_run_id AND s.snapshot_id=r.snapshot_id
          AND s.subject_id=? AND s.period_end=? AND r.report_type=?
          AND a.release_scope='internal_shareholder' AND a.artifact_release_id<>?
          AND a.superseded_by_artifact_release_id IS NULL
        RETURNING a.artifact_release_id''',
        (now,actor,new_id,tenant_id,subject_id,period_end,report_type,new_id)).fetchall()
