"""Qualify candidate currency correction on frozen QA data without saving changes."""
import hashlib,json,sys,types
from collections import defaultdict
from decimal import Decimal
import qa_read_only as q

def main():
    sys.path[:0]=['/app/src','/app/scripts']
    module=types.ModuleType('ibkr_etl.reporting_currency_holdings')
    module.__package__='ibkr_etl'
    exec(CANDIDATE_SOURCE,module.__dict__)
    results=[]
    with q.connect(q.TEST_DB) as db:
        class Adapter:
            def execute(self,sql,args=()):
                if not sql.lstrip().upper().startswith('SELECT '):raise ValueError('SELECT only')
                return db.execute(sql.replace('?', '%s'),args)
        rows=db.execute('SELECT subject_id,data_sha256,snapshot_payload_json FROM reporting_snapshots ORDER BY subject_id').fetchall()
        before={r['subject_id']:r['data_sha256'] for r in rows}
        if before!=EXPECTED_HASHES:raise ValueError('Frozen input drift')
        for row in rows:
            if row['subject_id'] not in ('U16088571','U16088606','hong-kong-china'):continue
            raw=row['snapshot_payload_json']
            if hashlib.sha256(raw.encode()).hexdigest()!=row['data_sha256']:raise ValueError('Snapshot hash mismatch')
            p=json.loads(raw)
            tools={'holdings':{'as_of':p['data_as_of'],'data':p['holdings']},'sector_allocation':{'data':p['sector_allocation']}}
            corrected=module.normalize_collected_report_holdings(Adapter(),row['subject_id'],p['reporting_currency'],tools)
            native=defaultdict(Decimal);converted=Decimal(0)
            for r in corrected['holdings']['data']['holdings']:
                native[r['native_currency']]+=abs(Decimal(r['native_market_value_text']))
                value=Decimal(r['native_market_value_text'])*Decimal(r['reporting_fx']['rate'])
                if abs(value-Decimal(r['market_value_text']))>Decimal('0.00000001'):raise ValueError('Independent multiplication differs')
                converted+=abs(value)
            s=corrected['sector_allocation']['data']
            if abs(float(converted)-s['gross_market_value'])>1e-8 or abs(sum(r['weight'] for r in s['allocation'])-1)>1e-10:raise ValueError('Converted totals do not reconcile')
            results.append({'subject':row['subject_id'],'holdings':len(p['holdings']['holdings']),
                'native_gross_by_currency':{k:str(v) for k,v in native.items()},'old_gross_unconverted':p['sector_allocation']['gross_market_value'],
                'corrected_gross':str(converted),'currency':p['reporting_currency'],
                'old_largest_sector':p['sector_allocation']['allocation'][0],
                'corrected_largest_sector':s['allocation'][0],
                'nav_and_performance_changed':False,'snapshot_saved':False})
        after={r['subject_id']:r['data_sha256'] for r in db.execute('SELECT subject_id,data_sha256 FROM reporting_snapshots').fetchall()}
        if after!=before:raise ValueError('Frozen inputs changed')
    print('UPGRADE_REPORT_CURRENCY '+json.dumps({'passed':True,'subjects':results,'database_writes':0,'production_connection_used':False,
        'candidate_qualified_against_frozen_inputs':True,'deployed':False,'corrected_rendering_verified':False}),flush=True)

if __name__=='__main__':main()
