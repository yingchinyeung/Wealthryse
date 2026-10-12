"""Qualify missing monthly analytics against immutable historical NAV, read-only."""
import hashlib,json,os,socket,sys,unittest
from pathlib import Path
import prepare_reporting_migration_clone_20261011 as q
from reconcile_legacy_monthly_reporting_20261011 import enrich
from ibkr_etl.monthly_reporting_analytics import build_monthly_reporting_analytics
from ibkr_etl.util import canonical_json
def sha(p):return hashlib.sha256(canonical_json(p).encode()).hexdigest()
def main():
    if os.getuid()!=10001 or q.TEST_DB!='upgrade_g04_migration_20261011' or {r[4][0] for r in socket.getaddrinfo(q.HOST,5432,type=socket.SOCK_STREAM)}!={'10.50.11.4'}:raise ValueError('Exact private QA required')
    for name,digest in SOURCE_HASHES.items():
        if hashlib.sha256((Path('/app')/name).read_bytes()).hexdigest()!=digest:raise ValueError('Installed candidate drift')
    suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[n]) for n in TEST_MODULES)
    tested=unittest.TextTestRunner(verbosity=0).run(suite)
    if not tested.wasSuccessful() or tested.testsRun!=24:raise ValueError('Exact enrichment native tests required')
    results=[]
    with q.connect(q.TEST_DB) as db:
        def state():return {t:hashlib.sha256('\n'.join(r['h'] for r in db.execute('SELECT md5(row_to_json(t)::text) AS h FROM '+t+' t ORDER BY 1').fetchall()).encode()).hexdigest() for t in q.REPORT_TABLES}
        if state()!=EXPECTED_REPORT_HASHES:raise ValueError('QA state changed')
        class Adapter:
            def execute(self,query,args=()):
                if not query.lstrip().upper().startswith('SELECT '):raise ValueError('SELECT only')
                return db.execute(query.replace('?','%s'),args)
        for plan in EXPECTED_MONTHS:
            rows=db.execute("SELECT snapshot_id,data_sha256,snapshot_payload_json FROM reporting_snapshots WHERE tenant_id=%s AND subject_id='U16088571' AND period_end=%s AND status IN ('frozen','approved')",(q.TENANT,plan['period_end'])).fetchall()
            if len(rows)!=1:raise ValueError('One active legacy snapshot required')
            row=rows[0];p=json.loads(row['snapshot_payload_json'])
            if hashlib.sha256(row['snapshot_payload_json'].encode()).hexdigest()!=row['data_sha256']:raise ValueError('Frozen hash differs')
            fresh=build_monthly_reporting_analytics(Adapter(),subject_id='U16088571',period_end=plan['period_end'],reporting_currency='USD')
            if sha(fresh)!=plan['rebuilt_sha256']:raise ValueError('Reconstruction differs from readiness proof')
            candidate=enrich(p,fresh)
            again=enrich(p,build_monthly_reporting_analytics(Adapter(),subject_id='U16088571',period_end=plan['period_end'],reporting_currency='USD'))
            if candidate!=again:raise ValueError('Enrichment replay differs')
            result={'subject':'U16088571','period_end':plan['period_end'],'source_snapshot_id':str(row['snapshot_id']),'source_sha256':row['data_sha256'],
                'candidate_sha256':sha(candidate),'calculation_sha256':sha(fresh),'frozen_nav_risk_returns_unchanged':True,'monthly_reconciliation_passed':True}
            results.append(result);print('LEGACY_MONTHLY_ITEM '+canonical_json(result),flush=True)
        if state()!=EXPECTED_REPORT_HASHES:raise ValueError('QA state changed')
    if len(results)!=20:raise ValueError('Exactly twenty legacy reconstructions required')
    print('LEGACY_MONTHLY_FINAL '+json.dumps({'passed':True,'months':20,'native_tests':24,'replay_passed':True,'frozen_nav_reconciliation_passed':True,
        'database_writes':0,'production_connection_used':False,'source_data_changed':False,'plan_sha256':sha(results),'enrichment_committed':False}),flush=True)
if __name__=='__main__':
    try:main()
    except Exception as e:
        print('LEGACY_MONTHLY_ERROR '+json.dumps({'type':type(e).__name__,'message':str(e)[:350]}),flush=True);raise SystemExit(1)
