"""Read-only exact date/coverage diagnosis; never accept unverified enrichment."""
import hashlib,json
from datetime import date
import prepare_reporting_migration_clone_20261011 as q
from reconcile_legacy_monthly_reporting_20261011 import enrich
from ibkr_etl.monthly_reporting_analytics import build_monthly_reporting_analytics
from ibkr_etl.util import canonical_json
def main():
    results=[]
    with q.connect(q.TEST_DB) as db:
        def state():return {t:hashlib.sha256('\n'.join(r['h'] for r in db.execute('SELECT md5(row_to_json(t)::text) AS h FROM '+t+' t ORDER BY 1').fetchall()).encode()).hexdigest() for t in q.REPORT_TABLES}
        if state()!=EXPECTED_REPORT_HASHES:raise ValueError('QA changed')
        class Adapter:
            def execute(self,query,args=()):
                if not query.lstrip().upper().startswith('SELECT '):raise ValueError('SELECT only')
                return db.execute(query.replace('?','%s'),args)
        for plan in EXPECTED_MONTHS:
            row=db.execute("SELECT snapshot_payload_json FROM reporting_snapshots WHERE tenant_id=%s AND subject_id='U16088571' AND period_end=%s AND status IN ('frozen','approved')",(q.TENANT,plan['period_end'])).fetchone()
            p=json.loads(row['snapshot_payload_json']);fresh=build_monthly_reporting_analytics(Adapter(),subject_id='U16088571',period_end=plan['period_end'],reporting_currency='USD')
            if hashlib.sha256(canonical_json(fresh).encode()).hexdigest()!=plan['rebuilt_sha256']:raise ValueError('Reconstruction changed')
            m=fresh['data'];start=date.fromisoformat(p['period_end']).replace(day=1).isoformat();series=p['performance']['series']
            before=[r for r in series if r['date']<start];inside=[r for r in series if start<=r['date']<=p['period_end']];selected=before[-1:]+inside
            try:enrich(p,fresh);failure=None
            except ValueError as error:failure=str(error)
            item={'period_end':p['period_end'],'failure':failure,'frozen_observations':len(selected)-1,'computed_observations':m['daily_observations'],
                'frozen_start':selected[1]['date'],'computed_start':m['period_start'],'frozen_end':selected[-1]['date'],'computed_end':m['period_end'],
                'frozen_month_return_matches':abs(float(m['portfolio_monthly_return'])-(float(selected[-1]['nav'])/float(selected[0]['nav'])-1))<=1e-10}
            results.append(item);print('LEGACY_WINDOW_ITEM '+canonical_json(item),flush=True)
        if state()!=EXPECTED_REPORT_HASHES:raise ValueError('QA changed')
    print('LEGACY_WINDOW_FINAL '+json.dumps({'passed':True,'diagnostic_only':True,'cases':len(results),'failed_checks':sum(bool(r['failure']) for r in results),
        'database_writes':0,'production_connection_used':False,'enrichment_accepted':False}),flush=True)
if __name__=='__main__':
    try:main()
    except Exception as e:
        print('LEGACY_WINDOW_ERROR '+json.dumps({'type':type(e).__name__,'message':str(e)[:300]}),flush=True);raise SystemExit(1)
