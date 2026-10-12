"""Private, offline cache rehearsal. No Azure, source or published-report writes."""
from pathlib import Path
import argparse
import hashlib
import json
import math
import os
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests')]
from test_alpha_world_ms_cache import CacheFixture
from ibkr_etl.risk_snapshots import build_risk_snapshot, snapshot_read_status, RiskInputsNotReady
from ibkr_etl.alpha_world_ms_cache import methodology_identity


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision',type=int,default=1,choices=range(1,100))
    args=parser.parse_args()
    os.environ.pop('DATABASE_URL',None)
    captured=ROOT/'.work/alpha-ms-hkd-hibor-20261007'
    pins={'hkd-history.json':'6dbc9ecfc858094571cd66568f883b6f91bbd69501aa9d09cbfb4f6a208c6661',
        'official-hibor.json':'9bef4e571e2564c5f0d2a462882ab29785d098df9397edc63b584830c6a52edf'}
    inputs={}
    for name,digest in pins.items():
        raw=(captured/name).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('Pinned input changed: '+name)
        inputs[name]=json.loads(raw)
    folder=ROOT/f'.work/alpha-ms-step3-20261007-r{args.revision}'
    folder.mkdir(exist_ok=False)
    c=CacheFixture()
    try:
        c.raw.execute('DELETE FROM subject_nav_series')
        c.raw.execute('DELETE FROM risk_free_rates')
        history=inputs['hkd-history.json']['series']
        for row in history:
            c.raw.execute('INSERT INTO subject_nav_series VALUES (?,?,?,?,?)',
                ('tenant','alpha-world-ms',row['date'],'HKD',str(row['nav'])))
        # Wrong-currency records are deliberately retained alongside HKD.
        c.raw.execute("INSERT INTO subject_nav_series VALUES ('tenant','alpha-world-ms','2026-10-01','USD','999')")
        for row in inputs['official-hibor.json']:
            c.raw.execute("INSERT INTO risk_free_rates VALUES ('tenant',?,'HKAB-HIBOR-1M','HKD','1-month',?,'pass')",(row['rate_date'],row['annual_rate']))
        c.commit()
        results=[];unavailable=[]
        for row in history:
            day=row['date']
            try:
                result=build_risk_snapshot(c,tenant_id='tenant',subject_id='alpha-world-ms',as_of=day,only_if_changed=True)
            except RiskInputsNotReady as error:
                if day not in [r['date'] for r in history[:2]]:raise
                unavailable.append({'date':day,'reason':str(error)})
                continue
            if result['status']!='rebuilt':raise ValueError('First pass did not rebuild '+day)
            record=c.record(day)
            if snapshot_read_status(record,subject_id='alpha-world-ms',as_of=day)!='current':raise ValueError('Cache failed '+day)
            results.append({'as_of':day,'source_data_sha256':record['source_data_sha256'],'payload_sha256':record['payload_sha256']})
        unchanged=0
        for item in results:
            result=build_risk_snapshot(c,tenant_id='tenant',subject_id='alpha-world-ms',as_of=item['as_of'],only_if_changed=True)
            if result['status']!='unchanged':raise ValueError('Replay was not idempotent')
            unchanged+=1
        september=json.loads(c.record('2026-09-30')['payload_json'])
        wanted={'total_return':.24705796825104676,'annualized_return':.1393859692527173,
            'annualized_volatility':.1507187602046211,'sharpe_ratio':.7372126538815966,
            'maximum_drawdown':-.12952427743668793,'var_95_daily':.012879343661685066,
            'cvar_95_daily':.019599962029404897,'win_rate':.5271493212669683,
            'risk_free_annual_rate':.02646383662650602}
        for key,value in wanted.items():
            if not math.isclose(september['metrics'][key],value,abs_tol=1e-12,rel_tol=1e-12):
                raise ValueError('September metric differs: '+key)
        if c.raw.execute('SELECT payload FROM immutable_published_reports').fetchone()[0]!='unchanged':raise ValueError('Published fixture changed')
        for name,digest in pins.items():
            if hashlib.sha256((captured/name).read_bytes()).hexdigest()!=digest:raise ValueError('Source file changed')
        paths=['src/ibkr_etl/'+n+'.py' for n in ('alpha_world_ms_cache','alpha_world_ms_hkd',
            'alpha_world_ms_analytics','risk_snapshots','risk_refresh','subject_read_contract','web',
            'hibor','market_calendar','subject_onboarding','derived_portfolio')]
        paths+=['scripts/build_risk_snapshots.py','scripts/qualify_alpha_ms_step3_cache.py',
            'tests/test_alpha_world_ms_cache.py','tests/test_risk_snapshots.py']
        record={'status':'private-candidate-qualified-not-deployed','step':3,
            'methodology_identity':methodology_identity(),'captured_inputs_sha256':pins,
            'source_files_sha256':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths},
            'nav_observations':len(history),'rebuilt':len(results),'unchanged_on_replay':unchanged,
            'unavailable_initial_dates':unavailable,'cache_manifest':results,
            'september_metrics':september['metrics'],'september_analytics_evidence':september['analytics_evidence'],
            'published_fixture_unchanged':True,'pinned_input_files_unchanged':True,
            'production_changed':False,'ibkr_source_changed':False,'azure_changed':False,
            'limitations':['Disposable SQLite only; PostgreSQL isolation, grants and worker locks still require step 4',
                'Recorded private inputs, not independent certification of broker/provider originals',
                'No report rendering, release, recurring-job installation or production cutover',
                'Unqualified HIBOR + Spread comparison, crisis/tail attribution and one-year drawdown withheld']}
        content=json.dumps(record,sort_keys=True,indent=2,allow_nan=False).encode()
        target=folder/'cache-qualification.json'
        with target.open('xb') as out:out.write(content)
        print(json.dumps({'record':str(target),'sha256':hashlib.sha256(content).hexdigest(),
            'rebuilt':len(results),'unchanged':unchanged,'unavailable_initial_dates':len(unavailable),
            'status':record['status']}))
    finally:c.raw.close()


if __name__=='__main__':main()
