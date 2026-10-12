"""Exercise versioned corrections and both rollback paths in one QA clone only."""
import hashlib,json,os,socket,sys,unittest
from pathlib import Path
from psycopg import sql
import prepare_reporting_migration_clone_20261011 as q
from prepare_reporting_snapshot_correction_20261011 import prepare
from ibkr_etl.reporting_currency_holdings import normalize_collected_report_holdings
from ibkr_etl.agent_tools import _sector_allocation
from ibkr_etl.util import canonical_json,utc_now_iso
STAGE='boundary'

def assert_nonreport(actual):
    if any(actual[k]!=BASELINE[k] for k in BASELINE if k!='reporting_snapshots'):raise ValueError('A non-snapshot table changed')

def original_rows(db):
    return {str(r['snapshot_id']):dict(r) for r in db.execute('SELECT * FROM reporting_snapshots').fetchall()}

def main():
    global STAGE
    if os.getuid()!=10001 or q.TEST_DB!='upgrade_g04_migration_20261011':raise ValueError('Exact unprivileged private QA clone required')
    if {r[4][0] for r in socket.getaddrinfo(q.HOST,5432,type=socket.SOCK_STREAM)}!={'10.50.11.4'}:raise ValueError('Wrong private endpoint')
    for n,h in SOURCE_HASHES.items():
        if hashlib.sha256((Path('/app')/n).read_bytes()).hexdigest()!=h:raise ValueError('Installed candidate drift')
    suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[n]) for n in TEST_MODULES)
    tests=unittest.TextTestRunner(verbosity=0).run(suite)
    if not tests.wasSuccessful() or tests.testsRun!=46:raise ValueError('Exact native migration tests required')
    if q.fingerprint(q.TEST_DB)!=BASELINE or q.fingerprint(q.SOURCE_DB)!=BASELINE:raise ValueError('QA source or clone drift')
    expected={r['snapshot_id']:r for r in INVENTORY['rows']}
    if len(expected)!=105 or any(r.get('blocker') for r in expected.values()):raise ValueError('Exact complete historical plan required')
    pairs=[];now=utc_now_iso();STAGE='prepare'
    with q.connect(q.TEST_DB,readonly=False) as db:
        if db.execute('SELECT current_database() AS d').fetchone()['d']!=q.TEST_DB:raise ValueError('Write connection escaped QA boundary')
        before=original_rows(db)
        active={k:r for k,r in before.items() if r['status'] in ('frozen','approved') and r['subject_id'] in {v['subject'] for v in expected.values()}}
        if set(active)!=set(expected):raise ValueError('Active QA history differs')
        class Adapter:
            def execute(self,query,args=()):
                if not query.lstrip().upper().startswith('SELECT '):raise ValueError('Correction inputs must be SELECT only')
                return db.execute(query.replace('?','%s'),args)
        adapter=Adapter()
        def detail(sid,date):return _sector_allocation(adapter,sid,date,{}).as_dict()['data']
        for key,plan in expected.items():
            row=active[key]
            if row['data_sha256']!=plan['source_sha256'] or row['status']!=plan['status']:raise ValueError('Original frozen version changed')
            if plan.get('needs_snapshot_correction'):
                new=prepare(row,plan,adapter,normalize_collected_report_holdings,detail,now)
                if str(new['snapshot_id']) in before:raise ValueError('Preserve previous migration attempt')
                pairs.append((row,new))
        if len(pairs)!=63:raise ValueError('Expected exactly 63 corrected snapshots')
        db.rollback()
        def transition(row,old,new):
            result=db.execute('UPDATE reporting_snapshots SET status=%s WHERE snapshot_id=%s AND tenant_id=%s AND status=%s AND data_sha256=%s',
                (new,row['snapshot_id'],q.TENANT,old,row['data_sha256']))
            if result.rowcount!=1:raise ValueError('Compare-and-set transition failed')
        def insert_and_activate():
            for old,new in pairs:
                columns=list(new)
                db.execute(sql.SQL('INSERT INTO reporting_snapshots ({}) VALUES ({})').format(sql.SQL(',').join(map(sql.Identifier,columns)),sql.SQL(',').join(sql.Placeholder() for _ in columns)),[new[k] for k in columns])
                transition(old,old['status'],'superseded');transition(new,'draft','frozen')
        def verify(candidate_active):
            current=original_rows(db)
            if set(current)!=set(before)|{str(new['snapshot_id']) for _,new in pairs}:raise ValueError('Snapshot scope changed')
            replaced={str(old['snapshot_id']) for old,_ in pairs}
            for key,old in before.items():
                desired=dict(old)
                if candidate_active and key in replaced:desired['status']='superseded'
                if current[key]!=desired:raise ValueError('Original snapshot content or approval evidence changed')
            for _,new in pairs:
                desired=dict(new);desired['status']='frozen' if candidate_active else 'superseded'
                if current[str(new['snapshot_id'])]!=desired:raise ValueError('Candidate changed or inherited approval')
            active_rows=[r for r in current.values() if r['status'] in ('frozen','approved') and r['subject_id'] in {v['subject'] for v in expected.values()}]
            if len(active_rows)!=105 or len({(r['subject_id'],r['period_end']) for r in active_rows})!=105:raise ValueError('Exactly one active version per month required')
        STAGE='transactional-rollback'
        insert_and_activate();verify(True);db.rollback()
        if original_rows(db)!=before:raise ValueError('Transactional rollback did not restore original rows')
        db.rollback()
        if q.fingerprint(q.TEST_DB)!=BASELINE:raise ValueError('Transactional rollback did not restore all 97 tables')
        STAGE='committed-apply'
        insert_and_activate();verify(True);db.commit()
        assert_nonreport(q.fingerprint(q.TEST_DB))
        STAGE='committed-rollback'
        for old,new in pairs:transition(new,'frozen','superseded');transition(old,'superseded',old['status'])
        verify(False);db.commit()
        assert_nonreport(q.fingerprint(q.TEST_DB))
        STAGE='candidate-reactivation'
        for old,new in pairs:transition(old,old['status'],'superseded');transition(new,'superseded','frozen')
        verify(True);db.commit()
    final=q.fingerprint(q.TEST_DB);assert_nonreport(final)
    if q.fingerprint(q.SOURCE_DB)!=BASELINE:raise ValueError('Original QA baseline changed')
    print('REPORTING_MIGRATION_FINAL '+json.dumps({'passed':True,'database':q.TEST_DB,'native_tests':tests.testsRun,'corrected_snapshots':63,'active_snapshots':105,
        'transactional_rollback_passed':True,'committed_rollback_passed':True,'original_snapshot_content_preserved':True,'existing_report_approvals_preserved':True,
        'non_snapshot_tables_unchanged':96,'source_qa_unchanged':True,'nav_returns_risk_unchanged':True,'prior_approvals_transferred':False,
        'final_snapshot_fingerprint':final['reporting_snapshots'],'baseline_fingerprint_sha256':hashlib.sha256(canonical_json(BASELINE).encode()).hexdigest(),
        'production_connection_used':False,'production_migration_executed':False,'private_data_uploaded_to_build_service':False,
        'reports_generated':0,'fourteen_day_observation':'UNVERIFIED','real_viewer_sign_in':'UNVERIFIED'}),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('REPORTING_MIGRATION_ERROR '+json.dumps({'type':type(e).__name__,'stage':STAGE,'message':str(e)[:350] if isinstance(e,ValueError) else None,'reconcile_before_retry':True}),flush=True)
        raise SystemExit(1)
