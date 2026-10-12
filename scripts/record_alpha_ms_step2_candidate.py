"""Local evidence only: a cutover plan, not an approval or database migration."""
import copy
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from ibkr_etl.subject_onboarding import approved_reference_definitions, configuration_sha256


def main():
    capture=ROOT/'.work/alpha-ms-calculation-audit-20261007/live-inspection.json'
    raw=capture.read_bytes()
    if hashlib.sha256(raw).hexdigest()!='17762831655cda49cc10d8364a3b5c8a291b0c757a91713d15cc818f62436919':
        raise ValueError('Reviewed live baseline capture differs; re-audit before planning')
    baseline=json.loads(raw)
    contracts=[r for r in baseline['contracts'] if r['subject_id']=='alpha-world-ms' and r['approval_status']=='approved' and r['effective_to'] is None]
    subjects=[r for r in baseline['subjects'] if r['subject_id']=='alpha-world-ms']
    if len(contracts)!=1 or len(subjects)!=1:raise ValueError('Ambiguous baseline')
    previous=contracts[0]
    old=previous['configuration_json']
    old=json.loads(old) if isinstance(old,str) else old
    if configuration_sha256(old)!=previous['configuration_sha256']:raise ValueError('Baseline contract hash differs')
    proposed=copy.deepcopy(old);proposed['base_currency']='HKD'
    if proposed!=approved_reference_definitions()['alpha-world-ms']:
        raise ValueError('Currency transition unexpectedly changes another financial convention')
    files=('subject_read_contract','agent_tools','alpha_world_ms_hkd','derived_portfolio',
        'report','reporting_snapshot','monthly_reporting_analytics','web')
    record={'status':'candidate-tested-not-deployed','step':2,'source_capture_sha256':hashlib.sha256(raw).hexdigest(),
        'previous_definition_version':previous['definition_version'],
        'proposed_definition_version':int(previous['definition_version'])+1,
        'previous_configuration_sha256':previous['configuration_sha256'],
        'proposed_configuration_sha256':configuration_sha256(proposed),'proposed_configuration':proposed,
        'metadata_changes':{'platform_subjects':{'base_currency':'HKD','reporting_currency':'HKD'},
            'derived_portfolios':{'base_currency':'HKD'}},
        'lifecycle_status_to_preserve':subjects[0]['lifecycle_status'],
        'benchmarks':'Currency alignment required; HIBOR + Spread convention remains unresolved and must not be fabricated',
        'mutable_analytics':'Only HKD-derived rows may be recalculated; retain legacy USD NAV and frozen/published snapshots',
        'next_gates':['methodology-aware cache validation','isolated PostgreSQL/staging acceptance',
            'financial and visual acceptance','verified rollback','coordinated release'],
        'production_changed':False,'ibkr_source_changed':False,'approval_record_created':False,
        'candidate_source_sha256':{name:hashlib.sha256((ROOT/'src/ibkr_etl'/(name+'.py')).read_bytes()).hexdigest() for name in files}}
    content=json.dumps(record,sort_keys=True,indent=2,allow_nan=False).encode()
    folder=ROOT/'.work/alpha-ms-step2-20261007';folder.mkdir(exist_ok=True)
    target=folder/'candidate-record.json'
    if target.exists() and target.read_bytes()!=content:raise ValueError('Candidate evidence already exists with different contents')
    if not target.exists():target.open('xb').write(content)
    print(json.dumps({'status':record['status'],'record':str(target),'sha256':hashlib.sha256(content).hexdigest(),
        'configuration_sha256':record['proposed_configuration_sha256']}))


if __name__=='__main__':main()
