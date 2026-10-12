"""Independent actual-input qualification; create-only private outputs."""
import copy
import hashlib
import json
from pathlib import Path
from ibkr_etl.alpha_world_ms_crisis import from_capture,qualified_snapshot,VERSION
from ibkr_etl.alpha_world_ms_presentation import build_model
from ibkr_etl.alpha_world_ms_analytics import analyze
from ibkr_etl.internal_release import approve_internal_release
from ibkr_etl.util import canonical_json

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'.work/alpha-ms-crisis-release-20261008'

def main():
    raw=(FOLDER/'preflight-v2/inputs.json').read_bytes();records=json.loads(raw)
    output=FOLDER/'qualified-inputs-v1';output.mkdir()
    rows=[]
    for row in records['snapshots']:
        p=row['snapshot_payload_json'];p=json.loads(p) if isinstance(p,str) else p
        original=copy.deepcopy(p);replay=from_capture(records,p);new=qualified_snapshot(p,replay)
        assert original==p and analyze(new)==analyze(p)
        model=build_model(new);assert model['crisis_available'] and model['risk']['crisis_maximum_drawdown']==replay['maximum_drawdown']
        runs=[{'report_type':t,'snapshot_id':'private-test','generation_run_id':i,
               'status':'awaiting_approval','output_sha256':'a'*64} for i,t in enumerate(('factsheet','presentation'))]
        approve_internal_release({'snapshot_id':'private-test','subject_id':'alpha-world-ms','period_end':new['period_end'],
            'status':'frozen','quality_status':'warn','publication_blockers_json':new['publication_blockers']},runs,user_acceptance_recorded=True)
        data=canonical_json(new).encode();filename=new['period_end']+'.json'
        with (output/filename).open('xb') as stream:stream.write(data)
        rows.append({'period_end':new['period_end'],'original_snapshot_id':row['snapshot_id'],
            'original_sha256':row['data_sha256'],'qualified_sha256':hashlib.sha256(data).hexdigest(),
            'maximum_drawdown':replay['maximum_drawdown'],'sleeve_legacy_differences':{s:v['legacy_maximum_absolute_difference'] for s,v in replay['inputs']['sleeves'].items()},
            'nav_metrics_unchanged':True,'internal_policy_eligible':True})
    proof={'status':'passed','version':VERSION,'months':len(rows),'rows':rows,
           'capture_sha256':hashlib.sha256(raw).hexdigest(),'database_writes':0,'production_deployed':False}
    with (output/'verification.json').open('x',encoding='utf-8') as stream:json.dump(proof,stream,indent=2,sort_keys=True)
    print(json.dumps({'status':'passed','months':len(rows),'database_writes':0}))

if __name__=='__main__':main()
