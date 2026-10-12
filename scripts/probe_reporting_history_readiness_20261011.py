"""Read-only readiness and reconstruction feasibility for active QA history."""
import hashlib,json,os,socket,sys
from pathlib import Path
import prepare_reporting_migration_clone_20261011 as q
from ibkr_etl.util import canonical_json
from ibkr_etl.monthly_reporting_analytics import build_monthly_reporting_analytics
def sha(p):return hashlib.sha256(canonical_json(p).encode()).hexdigest()
def main():
    if os.getuid()!=10001 or q.TEST_DB!='upgrade_g04_migration_20261011' or {r[4][0] for r in socket.getaddrinfo(q.HOST,5432,type=socket.SOCK_STREAM)}!={'10.50.11.4'}:raise ValueError('Exact private QA required')
    for name,digest in SOURCE_HASHES.items():
        if hashlib.sha256((Path('/app')/name).read_bytes()).hexdigest()!=digest:raise ValueError('Installed source drift')
    results=[]
    with q.connect(q.TEST_DB) as db:
        def state():return {t:hashlib.sha256('\n'.join(r['h'] for r in db.execute('SELECT md5(row_to_json(t)::text) AS h FROM '+t+' t ORDER BY 1').fetchall()).encode()).hexdigest() for t in q.REPORT_TABLES}
        if state()!=EXPECTED_REPORT_HASHES:raise ValueError('Private QA state changed')
        class Adapter:
            def execute(self,query,args=()):
                if not query.lstrip().upper().startswith('SELECT '):raise ValueError('Read-only analytics required')
                return db.execute(query.replace('?','%s'),args)
        rows=db.execute("SELECT subject_id,period_end,snapshot_payload_json FROM reporting_snapshots WHERE tenant_id=%s AND status IN ('frozen','approved') AND subject_id=ANY(%s) ORDER BY subject_id,period_end",(q.TENANT,['U16088571','U16088606','hong-kong-china','alpha-world-ms','alpha-world-t'])).fetchall()
        if len(rows)!=105:raise ValueError('Expected 105 active periods')
        for row in rows:
            p=json.loads(row['snapshot_payload_json']);monthly=p.get('monthly_reporting');entry={'subject':row['subject_id'],'period_end':row['period_end'],'monthly_present':monthly is not None,'monthly_available':bool((monthly or {}).get('available'))}
            if not entry['monthly_available']:
                fresh=build_monthly_reporting_analytics(Adapter(),subject_id=row['subject_id'],period_end=row['period_end'],reporting_currency=p['reporting_currency'])
                entry.update(existing_keys=sorted(monthly or {}),rebuilt_available=bool(fresh['data'].get('available')),rebuilt_quality=fresh.get('quality_status'),rebuilt_message=fresh.get('message'),rebuilt_sha256=sha(fresh),
                    rebuilt_data_keys=sorted(fresh['data']),warnings=fresh['data'].get('warnings'),frozen_performance_keys=sorted(p.get('performance') or {}))
            results.append(entry)
        if state()!=EXPECTED_REPORT_HASHES:raise ValueError('QA metadata changed')
    from collections import Counter
    print('REPORTING_READINESS_FINAL '+json.dumps({'passed':True,'snapshots':105,'ready_by_subject':dict(Counter(r['subject'] for r in results if r['monthly_available'])),
        'unready':[r for r in results if not r['monthly_available']],'database_writes':0,'production_connection_used':False}),flush=True)
if __name__=='__main__':
    try:main()
    except Exception as e:
        print('REPORTING_READINESS_ERROR '+json.dumps({'type':type(e).__name__,'message':str(e)[:300]}),flush=True);raise SystemExit(1)
