"""Owner-authorized metadata-only HKD staging after exact temporary rehearsal.

Retains the old USD definition and revoked approval. The new HKD definition
is VALIDATED, not approved. Never writes NAV, source data, reports or access.
"""
import base64
import hashlib
import json
import os
import zlib
from datetime import datetime, timezone
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

TENANT='00000000-0000-0000-0000-000000000001'
SUBJECT='alpha-world-t'
CASE='e71119a4-882f-4f87-87ed-024f7ea9fe73'
PLAN_SHA='c75ead273b77dc3143c2bb2fe93aff62df5552be97d9a278179d39a1fe2f23fb'
PREFIX='review/alpha-world-t/hkd-configuration-20261009-v1/'
KEYS={
    'platform_subjects':('tenant_id','subject_id'),
    'account_onboarding_cases':('tenant_id','case_id'),
    'subject_calculation_definitions':('tenant_id','definition_id'),
    'account_onboarding_approvals':('tenant_id','approval_id'),
    'account_onboarding_checks':('tenant_id','case_id','check_id'),
    'portfolio_benchmark_mapping':('tenant_id','subject_id','benchmark_id','effective_from'),
}
READ_TABLES=(*KEYS,'derived_portfolios','subject_dependencies','subject_feature_states')


def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':'),default=str)
def sha(v):return hashlib.sha256(canonical(v).encode()).hexdigest()


def normalized(rows):
    result=[]
    for row in rows:
        item=dict(row)
        for k,v in item.items():
            if k.endswith('_at') and v is not None:item[k]=datetime.fromisoformat(str(v)).isoformat()
            elif not isinstance(v,(str,int,float,bool,dict,list,type(None))):item[k]=str(v)
        result.append(item)
    return sorted(result,key=canonical)


def predicate(table):
    field='case_id' if table=='account_onboarding_checks' else 'target_subject_id' if table=='account_onboarding_cases' else 'portfolio_id' if table=='derived_portfolios' else 'subject_id'
    return field, CASE if field=='case_id' else SUBJECT


def snapshot(c):
    return {t:normalized(list(c.execute('SELECT * FROM public.'+t+' WHERE tenant_id=%s AND '+predicate(t)[0]+'=%s',
                                     (TENANT,predicate(t)[1])))) for t in READ_TABLES}


def protected(c):
    return {t:sha(normalized(list(c.execute('SELECT * FROM public.'+t+' WHERE tenant_id=%s AND '+predicate(t)[0]+'<>%s',
                                           (TENANT,predicate(t)[1]))))) for t in READ_TABLES}


def main():
    mode=os.environ.get('T_HKD_CONFIGURATION_MODE')
    if mode not in ('apply','verify'):raise ValueError('Explicit apply or verify required')
    if os.environ.get('DEPLOYMENT_ENVIRONMENT','').lower()!='staging':raise ValueError('Wrong environment')
    plan=json.loads(zlib.decompress(base64.b64decode(os.environ['T_HKD_CONFIGURATION_PLAN'])))
    gate=json.loads(os.environ['T_HKD_CONFIGURATION_REHEARSAL'])
    if sha(plan)!=PLAN_SHA or gate['status']!='passed' or gate['plan_sha256']!=PLAN_SHA or not gate['rollback_verified'] or not gate['persistent_state_unchanged']:
        raise ValueError('Exact successful rollback rehearsal required')
    if set(plan['before'])!=set(READ_TABLES) or set(plan['after'])!=set(READ_TABLES):raise ValueError('Unexpected tables')
    expected={t:normalized(plan['after'][t]) for t in READ_TABLES}
    original={t:normalized(plan['before'][t]) for t in READ_TABLES}
    service=BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
    def put(name,record):
        raw=canonical(record).encode();blob=service.get_blob_client('internal-reports',PREFIX+name)
        blob.upload_blob(raw,overwrite=False,content_settings=ContentSettings(content_type='application/json'),metadata={'sha256':hashlib.sha256(raw).hexdigest()})
        if blob.download_blob().readall()!=raw:raise ValueError('Protected evidence readback differs')
        return {'blob':PREFIX+name,'sha256':hashlib.sha256(raw).hexdigest()}
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row,connect_timeout=25,
        options='-c statement_timeout=30000 -c lock_timeout=15000') as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE'+(' READ ONLY' if mode=='verify' else ''))
        c.execute("SELECT set_config('app.tenant_id',%s,true)",(TENANT,))
        role=c.execute('SELECT current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone()
        if role['rolsuper'] or role['rolbypassrls']:raise ValueError('Application role required')
        if mode=='verify':
            if snapshot(c)!=expected:raise ValueError('Committed HKD readback differs')
            c.rollback()
            result={'status':'verified_hkd_pending_approval','plan_sha256':PLAN_SHA,'database_persistent_writes':0}
        else:
            for key in ('ibkr-daily:','self-service-derived:','alpha-t-hkd-configuration:'):
                if not c.execute('SELECT pg_try_advisory_xact_lock(hashtext(%s)) AS locked',(key+TENANT,)).fetchone()['locked']:raise ValueError('Related processing busy')
            # Row locks also serialize website changes to this case and contract.
            for table in KEYS:
                field,value=predicate(table)
                list(c.execute('SELECT 1 FROM public.'+table+' WHERE tenant_id=%s AND '+field+'=%s FOR UPDATE',(TENANT,value)))
            if snapshot(c)!=original:raise ValueError('Inspected live configuration drifted; do not apply')
            for table,field in (('derived_portfolio_nav','portfolio_id'),('risk_analytics_snapshots','subject_id'),('reporting_snapshots','subject_id')):
                if c.execute('SELECT count(*) AS n FROM public.'+table+' WHERE tenant_id=%s AND '+field+'=%s',(TENANT,SUBJECT)).fetchone()['n']:
                    raise ValueError('T calculations or reports appeared; preserve them and replan')
            preserved=protected(c)
            backup=put('rollback-backup.json',{'plan':plan,'rehearsal':gate,'protected_fingerprints':preserved,
                                              'captured_at':datetime.now(timezone.utc).isoformat()})
            changes=0
            for table,keys in KEYS.items():
                prior={tuple(r[k] for k in keys):r for r in plan['before'][table]}
                for row in plan['after'][table]:
                    key=tuple(row[k] for k in keys);old=prior.get(key)
                    def adapt(v):return Jsonb(v) if isinstance(v,(dict,list)) else v
                    if old is None:
                        if table!='subject_calculation_definitions' or row['approval_status']!='validated':raise ValueError('Unexpected insertion')
                        cols=list(row)
                        cursor=c.execute('INSERT INTO public.'+table+' ('+','.join(cols)+') VALUES ('+','.join('%s' for _ in cols)+')',tuple(adapt(row[k]) for k in cols))
                    else:
                        cols=[k for k in row if row[k]!=old[k]]
                        if not cols:continue
                        cursor=c.execute('UPDATE public.'+table+' SET '+','.join(k+'=%s' for k in cols)+' WHERE '+' AND '.join(k+'=%s' for k in keys),tuple(adapt(row[k]) for k in cols)+key)
                    if cursor.rowcount!=1:raise ValueError('Exact one-row update required')
                    changes+=1
            if changes!=9 or snapshot(c)!=expected or protected(c)!=preserved:raise ValueError('Scoped correction readback differs')
            precommit=put('precommit.json',{'plan_sha256':PLAN_SHA,'after_sha256':sha(expected),
                                          'changed_rows':changes,'backup':backup,'other_subjects_unchanged':True})
            c.commit()
            result={'status':'hkd_staged_pending_approval','plan_sha256':PLAN_SHA,'changed_configuration_rows':changes,
                    'backup':backup,'precommit':precommit,'other_subjects_unchanged':True}
    result.update(captured_at=datetime.now(timezone.utc).isoformat(),new_contract_approved=False,
                  reports_released=False,viewer_access_changed=False,broker_source_data_changed=False)
    receipt=put(mode+'-receipt.json',result)
    print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({**result,'protected_receipt':receipt}),flush=True)


if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'stopped','error_class':type(exc).__name__,
            'error':str(exc) if isinstance(exc,ValueError) else 'Service detail redacted; reconcile outcome before retry'}),flush=True)
        raise SystemExit(1)
