"""Execute and roll back the exact correction in private PostgreSQL tables only.

No apply mode exists. Application DML explicitly names pg_temp and runs in a
read-only transaction. Public configuration and the audit history stay intact.
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

KEYS={
    'platform_subjects':('tenant_id','subject_id'),
    'account_onboarding_cases':('tenant_id','case_id'),
    'subject_calculation_definitions':('tenant_id','definition_id'),
    'account_onboarding_approvals':('tenant_id','approval_id'),
    'account_onboarding_checks':('tenant_id','case_id','check_id'),
    'portfolio_benchmark_mapping':('tenant_id','subject_id','benchmark_id','effective_from'),
}


def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':'),default=str)
def sha(v):return hashlib.sha256(canonical(v).encode()).hexdigest()


def normalized(rows):
    result=[]
    for row in rows:
        item=dict(row)
        for k,v in item.items():
            if k.endswith('_at') and v is not None:
                item[k]=datetime.fromisoformat(str(v)).isoformat()
            elif not isinstance(v,(str,int,float,bool,dict,list,type(None))):item[k]=str(v)
        result.append(item)
    return sorted(result,key=canonical)


def main():
    plan=json.loads(zlib.decompress(base64.b64decode(os.environ['T_HKD_CORRECTION_PLAN'])))
    if sha(plan)!=os.environ['T_HKD_CORRECTION_SHA256']:raise ValueError('Plan hash differs')
    tenant=plan['tenant_id'];subject=plan['subject_id'];case=plan['case_id']
    if subject!='alpha-world-t' or tenant!='00000000-0000-0000-0000-000000000001':raise ValueError('Wrong subject scope')
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row,connect_timeout=25,
        options='-c default_transaction_read_only=on -c statement_timeout=30000') as c:
        c.execute("SELECT set_config('app.tenant_id',%s,false)",(tenant,));c.commit()
        role=c.execute('SELECT current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone()
        if role['rolsuper'] or role['rolbypassrls']:raise ValueError('Application identity required')
        def fingerprint():
            return {t:sha(normalized(list(c.execute('SELECT * FROM public.'+t+' WHERE tenant_id=%s',(tenant,))))) for t in plan['before']}
        before=fingerprint()
        for table,expected in plan['before'].items():
            field='case_id' if table=='account_onboarding_checks' else 'target_subject_id' if table=='account_onboarding_cases' else 'portfolio_id' if table=='derived_portfolios' else 'subject_id'
            actual=list(c.execute('SELECT * FROM public.'+table+' WHERE tenant_id=%s AND '+field+'=%s',(tenant,case if field=='case_id' else subject)))
            if normalized(actual)!=normalized(expected):raise ValueError('Live evidence drift: '+table)
        c.commit();c.execute('SET TRANSACTION READ WRITE')
        for table in KEYS:
            c.execute('CREATE TEMP TABLE '+table+' (LIKE public.'+table+' INCLUDING ALL)')
            c.execute('INSERT INTO pg_temp.'+table+' SELECT * FROM public.'+table+' WHERE tenant_id=%s',(tenant,))
        c.commit()
        if c.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']!='on':raise ValueError('Read-only guard absent')
        c.execute('SAVEPOINT t_hkd_plan')
        changed=[]
        def adapt(v):return Jsonb(v) if isinstance(v,(dict,list)) else v
        for table,keys in KEYS.items():
            prior={tuple(r[k] for k in keys):r for r in plan['before'][table]}
            for row in plan['after'][table]:
                key=tuple(row[k] for k in keys);old=prior.get(key)
                if old is None:
                    if table!='subject_calculation_definitions' or row['approval_status']!='validated':raise ValueError('Unexpected insertion')
                    cols=list(row)
                    cursor=c.execute('INSERT INTO pg_temp.'+table+' ('+','.join(cols)+') VALUES ('+','.join('%s' for _ in cols)+')',tuple(adapt(row[k]) for k in cols))
                else:
                    cols=[k for k in row if row[k]!=old[k]]
                    if not cols:continue
                    cursor=c.execute('UPDATE pg_temp.'+table+' SET '+','.join(k+'=%s' for k in cols)+' WHERE '+' AND '.join(k+'=%s' for k in keys),tuple(adapt(row[k]) for k in cols)+key)
                if cursor.rowcount!=1:raise ValueError('Expected one exact row mutation')
                changed.append(table)
            field='case_id' if table=='account_onboarding_checks' else 'target_subject_id' if table=='account_onboarding_cases' else 'subject_id'
            actual=list(c.execute('SELECT * FROM pg_temp.'+table+' WHERE tenant_id=%s AND '+field+'=%s',(tenant,case if field=='case_id' else subject)))
            if normalized(actual)!=normalized(plan['after'][table]):raise ValueError('Candidate readback differs: '+table)
        c.execute('ROLLBACK TO SAVEPOINT t_hkd_plan')
        for table in KEYS:
            restored=normalized(list(c.execute('SELECT * FROM pg_temp.'+table+' WHERE tenant_id=%s',(tenant,))))
            if sha(restored)!=before[table]:raise ValueError('Rollback mismatch: '+table)
        if fingerprint()!=before:raise ValueError('Persistent state changed')
        c.rollback()
    print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'passed','captured_at':datetime.now(timezone.utc).isoformat(),
        'plan_sha256':sha(plan),'changed_private_rows':len(changed),'rollback_verified':True,
        'persistent_state_unchanged':True,'database_persistent_writes':0,
        'new_contract_approved':False,'viewer_access_changed':False,'role':role}),flush=True)


if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'failed','error_class':type(exc).__name__,
            'error':str(exc) if isinstance(exc,ValueError) else 'Diagnostic service detail redacted'}),flush=True)
        raise SystemExit(1)
