"""Qualify all T months under the established internal hypothetical-stress policy."""
from pathlib import Path
import hashlib,json,math
import xml.etree.ElementTree as ET
from ibkr_etl.alpha_world_t_crisis import from_capture, qualified_snapshot
from ibkr_etl.alpha_world_t_analytics import analyze
from ibkr_etl.internal_release import approve_internal_release

ROOT=Path(__file__).resolve().parents[1]
W=ROOT/'.work/alpha-t-onboarding-completion-20261009'
OUT=ROOT/'.work/alpha-t-internal-crisis-20261009-v1'


def main():
    raw=(W/'supplemental-inputs.json').read_bytes()
    receipt=json.loads((W/'supplemental-inputs-result.json').read_bytes())
    assert hashlib.sha256(raw).hexdigest()==next(r['sha256'] for r in receipt['records'] if r.get('status')=='captured')
    supplemental=json.loads(raw)
    raw_live=(ROOT/'.work/alpha-world-t-accuracy-audit-20261009/live-inputs.json').read_bytes()
    assert hashlib.sha256(raw_live).hexdigest()=='2513a81af91b081eeaa38879aa7a4db1409d7abcb4d4ccb9a5e0a6674e0bf9a4'
    live=json.loads(raw_live)
    originals=json.loads((W/'broker-reconciliation.json').read_bytes())
    provenance={(r['account_id'],r['snapshot_date'],str(r['conid'])):r for r in originals['comparisons']['position_details']['matched']}
    positions=[{**r,'subject_id':r['account_id']} for r in live['position_details']]
    mapping_keys={(r['subject_id'],r['holdings_as_of'],str(r['conid'])) for r in supplemental['crisis_replay_components']}
    xml_cache={}
    for r in positions:
        key=(r['subject_id'],r['snapshot_date'],str(r['conid']))
        if key not in mapping_keys:
            proof=provenance[key];digest=proof['source_sha256']
            if digest not in xml_cache:
                content=(W/'originals'/(digest+'.xml')).read_bytes()
                assert hashlib.sha256(content).hexdigest()==digest
                xml_cache[digest]=ET.fromstring(content)
            matches=[e.attrib for e in xml_cache[digest].iter('OpenPosition') if e.get('conid')==str(r['conid'])
                     and e.get('reportDate')==r['snapshot_date'].replace('-','') and e.get('accountId')==r['account_id']]
            if matches and all(a==matches[0] for a in matches):r['broker_attributes']=matches[0]
    records={'source_nav':live['source_nav'],'source_positions':positions,
        'mappings':supplemental['crisis_replay_components'],'prices':supplemental['crisis_prices'],'broker_fx':live['broker_fx']}
    OUT.mkdir(exist_ok=False); results=[]
    for path in sorted((ROOT/'.work/alpha-t-presentations-qualification-v5/inputs').glob('*.json')):
        original=path.read_bytes();snapshot=json.loads(original)
        # Existing licensing/publication limits are retained. This local adapter
        # has no authority to approve a report or alter a database check.
        snapshot['publication_blockers']=['primary_benchmark:not_licensed_for_publication',
            'secondary_benchmark:not_licensed_for_publication','risk:crisis_replay_unavailable']
        replay=from_capture(records,snapshot)
        earlier=json.loads((W/'extended-risk-v3-proxy'/path.name).read_bytes())
        assert math.isclose(replay['maximum_drawdown'],earlier['crisis']['maximum_drawdown'],abs_tol=1e-12,rel_tol=0)
        qualified=qualified_snapshot(snapshot,replay)
        assert analyze(snapshot)==analyze(qualified)
        runs=[{'report_type':kind,'snapshot_id':'private-test','generation_run_id':i,
            'status':'awaiting_approval','output_sha256':'a'*64} for i,kind in enumerate(('factsheet','presentation'))]
        approve_internal_release({'snapshot_id':'private-test','subject_id':'alpha-world-t','period_end':path.stem,
            'status':'frozen','quality_status':'warn','publication_blockers_json':qualified['publication_blockers']},
            runs,user_acceptance_recorded=True)
        with (OUT/path.name).open('x',encoding='utf-8') as stream:json.dump(qualified,stream,indent=2,allow_nan=False)
        assert path.read_bytes()==original
        results.append({'period_end':path.stem,'maximum_drawdown':replay['maximum_drawdown'],
            'input_sha256':replay['input_sha256'],'proxy_components':replay['proxy_components'],
            'status':'qualified_internal','original_core_kpis_unchanged':True,
            'publication_allowed':False,'internal_policy_unit_test_passed':True})
    assert len(results)==21
    proof={'status':'passed','months':21,'results':results,'source_data_changed':False,'production_changed':False,
        'approvals_recorded':False,'independent_market_prices_verified':False,
        'policy':'Existing MS internal-only provisional hypothetical stress standard; T-specific calculations and owner-approved identity proxy'}
    with (OUT/'verification.json').open('x',encoding='utf-8') as stream:json.dump(proof,stream,indent=2)
    print(json.dumps({k:v for k,v in proof.items() if k!='results'}))


if __name__=='__main__':main()
