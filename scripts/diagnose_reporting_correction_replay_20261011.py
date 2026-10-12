"""Read-only comparison of the planned and extracted correction algorithms."""
import copy,hashlib,json,os
from ibkr_etl.util import canonical_json
from ibkr_etl.reporting_currency_holdings import normalize_collected_report_holdings as normalize
from ibkr_etl.agent_tools import _sector_allocation
from legacy_reporting_reconciliation_20261011 import reconcile_legacy_sector
import prepare_reporting_snapshot_correction_20261011 as correction
import prepare_reporting_migration_clone_20261011 as q
def sha(p):return hashlib.sha256(canonical_json(p).encode()).hexdigest()
def main():
    if q.fingerprint(q.TEST_DB)!=BASELINE or q.fingerprint(q.SOURCE_DB)!=BASELINE:raise ValueError('QA baseline changed')
    results=[]
    with q.connect(q.TEST_DB) as db:
        class Adapter:
            def execute(self,query,args=()):
                if not query.lstrip().upper().startswith('SELECT '):raise ValueError('Read-only required')
                return db.execute(query.replace('?','%s'),args)
        adapter=Adapter()
        for plan in INVENTORY['rows']:
            if not plan.get('needs_snapshot_correction'):continue
            row=dict(db.execute('SELECT * FROM reporting_snapshots WHERE snapshot_id=%s',(plan['snapshot_id'],)).fetchone())
            p=json.loads(row['snapshot_payload_json'])
            if p['subject_id']=='U16088571' and 'classifications' not in p['sector_allocation'] and 'allocation_detail' not in p['sector_allocation']:
                detail=_sector_allocation(adapter,p['subject_id'],p['data_as_of'],{}).as_dict()['data']
                p['sector_allocation']=reconcile_legacy_sector(p,detail)
                v=p['sector_allocation']['classification_detail_provenance']
                p['source_citations']['sector_allocation'].append({'table':'instrument_classifications','method':v['method'],'reference_sha256':v['reference_sha256'],'checked_at':v['reference_checked_at']})
            values=normalize(adapter,p['subject_id'],p['reporting_currency'],{'holdings':{'as_of':p['data_as_of'],'data':p['holdings']},'sector_allocation':{'data':p['sector_allocation']}})
            for key in ('holdings','sector_allocation'):
                p[key]=values[key]['data'];p['source_citations'][key]=[*p['source_citations'][key],*values[key].get('citations',[])]
            revised={**plan,'candidate_sha256':sha(p)}
            matched=True
            try:correction.prepare(row,revised,adapter,normalize,lambda s,d:_sector_allocation(adapter,s,d,{}).as_dict()['data'],'diagnostic-only')
            except ValueError:matched=False
            results.append({'subject':plan['subject'],'period':plan['period_end'],'planned_sha256':plan['candidate_sha256'],'replayed_sha256':sha(p),
                'matches_plan':sha(p)==plan['candidate_sha256'],'extracted_algorithm_matches_reference':matched})
    if q.fingerprint(q.TEST_DB)!=BASELINE or q.fingerprint(q.SOURCE_DB)!=BASELINE:raise ValueError('Read-only diagnosis changed QA')
    print('REPORTING_MIGRATION_FINAL '+json.dumps({'passed':True,'diagnostic_only':True,'database_writes':0,'production_connection_used':False,
        'cases':len(results),'mismatches':[r for r in results if not r['matches_plan'] or not r['extracted_algorithm_matches_reference']]}),flush=True)
if __name__=='__main__':
    try:main()
    except Exception as e:
        print('REPORTING_MIGRATION_ERROR '+json.dumps({'type':type(e).__name__,'message':str(e)[:300]}),flush=True);raise SystemExit(1)
