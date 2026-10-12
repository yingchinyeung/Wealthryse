"""Identify exact legacy replay ordering differences, with no database writes."""
import copy,hashlib,json
from collections import Counter
from ibkr_etl.util import canonical_json
from ibkr_etl.reporting_currency_holdings import normalize_collected_report_holdings as normalize
from ibkr_etl.agent_tools import _sector_allocation
from legacy_reporting_reconciliation_20261011 import reconcile_legacy_sector
import prepare_reporting_migration_clone_20261011 as q
def sha(p):return hashlib.sha256(canonical_json(p).encode()).hexdigest()
def paths(left,right,path=''):
    if type(left)!=type(right):return [path]
    if isinstance(left,dict):return [p for key in set(left)|set(right) for p in (paths(left[key],right[key],path+'/'+key) if key in left and key in right else [path+'/'+key])]
    if isinstance(left,list):return [path+'/length'] if len(left)!=len(right) else [p for i,(a,b) in enumerate(zip(left,right)) for p in paths(a,b,path+'/'+str(i))]
    return [] if left==right else [path]
def main():
    if q.fingerprint(q.TEST_DB)!=BASELINE or q.fingerprint(q.SOURCE_DB)!=BASELINE:raise ValueError('QA changed')
    plan=next(r for r in INVENTORY['rows'] if r['subject']=='U16088571' and r['period_end']=='2025-06-30')
    variants=[];payloads=[]
    for database,order in ((q.SOURCE_DB,''),(q.TEST_DB,''),(q.TEST_DB,'ASC'),(q.TEST_DB,'DESC')):
        with q.connect(database) as db:
            class Adapter:
                def execute(self,query,args=()):
                    if not query.lstrip().upper().startswith('SELECT '):raise ValueError('SELECT only')
                    if order and 'FROM subject_position_series p' in query and 'ORDER BY ABS' in query:
                        query=query.rstrip()+', p.conid '+order
                    return db.execute(query.replace('?','%s'),args)
            adapter=Adapter();row=db.execute('SELECT snapshot_payload_json FROM reporting_snapshots WHERE snapshot_id=%s',(plan['snapshot_id'],)).fetchone()
            p=json.loads(row['snapshot_payload_json']);detail=_sector_allocation(adapter,p['subject_id'],p['data_as_of'],{}).as_dict()['data']
            p['sector_allocation']=reconcile_legacy_sector(p,detail);v=p['sector_allocation']['classification_detail_provenance']
            p['source_citations']['sector_allocation'].append({'table':'instrument_classifications','method':v['method'],'reference_sha256':v['reference_sha256'],'checked_at':v['reference_checked_at']})
            data=normalize(adapter,p['subject_id'],p['reporting_currency'],{'holdings':{'as_of':p['data_as_of'],'data':p['holdings']},'sector_allocation':{'data':p['sector_allocation']}})
            for key in ('holdings','sector_allocation'):p[key]=data[key]['data'];p['source_citations'][key]=[*p['source_citations'][key],*data[key].get('citations',[])]
            counts=Counter(r['market_value'] for r in detail['classifications'])
            variants.append({'database':database,'tie_order':order or 'unspecified','matches_plan':sha(p)==plan['candidate_sha256'],'candidate_sha256':sha(p),'tie_group_sizes':[n for n in counts.values() if n>1]})
            payloads.append(p)
    differences=[{'variant':i,'paths':paths(payloads[0],p)} for i,p in enumerate(payloads[1:],1)]
    def comparable(p):
        p=copy.deepcopy(p)
        p['sector_allocation']['classifications'].sort(key=lambda r:r['conid'])
        p['sector_allocation']['classification_detail_provenance'].pop('reference_sha256')
        p['source_citations']['sector_allocation'][-2].pop('reference_sha256')
        return p
    equivalent=all(comparable(p)==comparable(payloads[0]) for p in payloads[1:])
    if q.fingerprint(q.TEST_DB)!=BASELINE or q.fingerprint(q.SOURCE_DB)!=BASELINE:raise ValueError('Read-only diagnosis changed QA')
    print('REPORTING_MIGRATION_FINAL '+json.dumps({'passed':True,'diagnostic_only':True,'cases':1,'database_writes':0,'production_connection_used':False,
        'variants':variants,'differences':differences,'equivalent_after_only_classification_order_and_reference_hash':equivalent}),flush=True)
if __name__=='__main__':
    try:main()
    except Exception as e:
        print('REPORTING_MIGRATION_ERROR '+json.dumps({'type':type(e).__name__,'message':str(e)[:300]}),flush=True);raise SystemExit(1)
