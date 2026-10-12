"""Create-only offline methodology qualification, never a production cutover."""
from datetime import date
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from ibkr_etl.alpha_world_ms_hkd import rebuild, factsheet_snapshot
from ibkr_etl.alpha_world_ms_analytics import analyze
from ibkr_etl.monthly_artifacts import render_factsheet
import numpy as np

FOLDER=ROOT/'.work/alpha-ms-hkd-hibor-20261007'
CAPTURE=ROOT/'.work/alpha-ms-factsheets-v13-20261007'
OUTPUT=ROOT/'output/pdf/AlphaWorld_MS_HKD_HIBOR_Validation_v14r8'


def sha(data):return hashlib.sha256(data).hexdigest()


def main():
    source=(FOLDER/'source-inspection.json').read_bytes()
    if sha(source)!='50d294c937fd29ddaa4c678b4eab27528dba815848c0a9ba48a245d4a47bacc4':
        raise ValueError('Source capture integrity differs')
    fixing_bytes=(FOLDER/'official-hibor.json').read_bytes()
    if sha(fixing_bytes)!='9bef4e571e2564c5f0d2a462882ab29785d098df9397edc63b584830c6a52edf':
        raise ValueError('Official HIBOR capture integrity differs')
    history=rebuild(json.loads(source))
    # Independent vector sum of recorded original source values and dated FX.
    audit=history['source_audit']
    nav={r['date']:r['nav'] for r in history['series']}
    for day in nav:
        observations=[r for r in audit if r['date']==day]
        expected=58+np.sum(np.array([r['coefficient'] for r in observations]) * 100 *
            np.array([r['source_value'] for r in observations]) * np.array([r['fx_to_hkd'] for r in observations]) /
            np.array([r['normalization_hkd'] for r in observations]))
        if not np.isclose(expected,nav[day],rtol=0,atol=1e-10):raise ValueError('Independent HKD NAV differs')
    capture=json.loads((CAPTURE/'capture.json').read_bytes())
    for asset in capture['assets']:
        if sha((CAPTURE/(asset['asset_role']+'.pdf')).read_bytes())!=asset['sha256']:
            raise ValueError('Approved template bytes differ')
    OUTPUT.mkdir(exist_ok=False)
    inputs=FOLDER/'hkd-inputs';inputs.mkdir(exist_ok=False)
    artifacts=[]
    for period in capture['expected_periods']:
        snapshot=factsheet_snapshot(history,json.loads(fixing_bytes),period)
        metrics,daily,evidence=analyze(snapshot)
        series=np.array([r['nav'] for r in snapshot['performance']['series']])
        rf=np.mean([r['annual_rate'] for r in snapshot['risk']['risk_free']['observations']])
        sigma=np.std(daily,ddof=1)
        threshold=np.quantile(daily,.05)
        expected={'total_return':series[-1]/series[0]-1,
            'annualized_return':(series[-1]/series[0])**(365.25/(date.fromisoformat(snapshot['data_as_of'])-date(2025,1,20)).days)-1,
            'annualized_volatility':sigma*np.sqrt(252),
            'sharpe_ratio':(np.mean(daily)-((1+rf)**(1/252)-1))/sigma*np.sqrt(252),
            'maximum_drawdown':np.min(series/np.maximum.accumulate(series)-1),
            'var_95_daily':max(0.,-threshold),'cvar_95_daily':max(0.,-np.mean(np.array(daily)[np.array(daily)<=threshold])),
            'win_rate':np.mean(np.array(daily)>0),'risk_free_annual_rate':rf}
        for key,value in expected.items():
            if not np.isclose(value,metrics[key],rtol=0,atol=1e-11):raise ValueError('Independent metric differs: '+key)
        data=json.dumps(snapshot,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        (inputs/(period+'.json')).open('xb').write(data)
        target=OUTPUT/('AlphaWorld_MS_Factsheet_'+period+'.pdf')
        result=render_factsheet(snapshot,template_path=CAPTURE/'template.pdf',design_model_path=CAPTURE/'completed_example.pdf',output_path=target)
        if json.dumps(snapshot,sort_keys=True,separators=(',',':'),allow_nan=False).encode()!=data:
            raise ValueError('Renderer mutated input')
        artifacts.append({'period_end':period,'data_sha256':sha(data),'pdf':str(target),'sha256':sha(target.read_bytes()),
            'metrics':metrics,'render':result})
        print(json.dumps({'period':period,'status':'private_hkd_validated','risk_free_average':rf}),flush=True)
    record={'status':'private_methodology_validated','subject_id':'alpha-world-ms','currency':'HKD','count':len(artifacts),
        'source_capture_sha256':sha(source),'official_hibor_sha256':sha(fixing_bytes),
        'official_hibor_fixings':415,'contract':history['contract'],'contract_sha256':history['contract_sha256'],
        'source_audit_sha256':history['source_audit_sha256'],'broker_data_changed':False,
        'published_history_changed':False,'production_cutover':False,'commercial_usage_rights_owner_confirmed':True,
        'risk_free_basis':'1M HIBOR; arithmetic mean of published fixings from inception through data_as_of',
        'artifacts':artifacts}
    (OUTPUT/'manifest.json').open('x',encoding='utf-8').write(json.dumps(record,indent=2))
    (FOLDER/'hkd-history.json').open('x',encoding='utf-8').write(json.dumps(history,sort_keys=True))
    print(json.dumps({'status':record['status'],'count':len(artifacts),'production_cutover':False}),flush=True)


if __name__=='__main__':main()
