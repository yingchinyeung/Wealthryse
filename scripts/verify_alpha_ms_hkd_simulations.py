"""Separate NumPy products verify every deterministic annualized MC path."""
from pathlib import Path
import hashlib
import json
import random
import sys
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from ibkr_etl.alpha_world_ms_analytics import analyze,simulate,VERSION


def main():
    folder=ROOT/'.work/alpha-ms-hkd-hibor-20261007'
    records=[]
    for path in sorted((folder/'hkd-inputs').glob('*.json')):
        snapshot=json.loads(path.read_bytes());_,daily,_=analyze(snapshot)
        actual,mean=simulate(daily,snapshot['period_end'])
        seed=int(hashlib.sha256(f"alpha-world-ms|{snapshot['period_end']}|{VERSION}".encode()).hexdigest()[:16],16)
        generator=random.Random(seed)
        indices=np.array([generator.randrange(len(daily)) for _ in range(1000*252)]).reshape(1000,252)
        expected=np.prod(1+np.array(daily)[indices],axis=1)-1
        if not np.allclose(expected,actual,rtol=0,atol=1e-12) or not np.isclose(mean,np.mean(expected),rtol=0,atol=1e-12):
            raise ValueError('Independent MC annualized distribution differs')
        records.append({'period_end':snapshot['period_end'],'paths':len(actual),'sessions_per_path':252,
            'maximum_absolute_difference':float(np.max(np.abs(expected-np.array(actual))))})
    record={'status':'pass','independently_checked_paths':sum(r['paths'] for r in records),
        'extra_interest_charge_applied':False,'model_version':VERSION,'artifacts':records}
    (folder/'private-simulation-qa.json').open('x',encoding='utf-8').write(json.dumps(record,indent=2))
    print(json.dumps({k:v for k,v in record.items() if k!='artifacts'}))


if __name__=='__main__':main()
