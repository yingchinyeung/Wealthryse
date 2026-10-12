"""Audited bounded restoration of original evidence, with rehearsal before apply."""
from pathlib import Path
import sys,json,hashlib,base64,zlib
import run_alpha_ms_recovery_operation as controller
from restore_t_receivable_evidence_20261009 import SOURCES,parse_sources
ROOT=Path(__file__).resolve().parents[1]
controller.FOLDER=ROOT/'.work/alpha-t-receivable-evidence-20261009-v2'
controller.FOLDER.mkdir(exist_ok=True)
mode,label=sys.argv[1:3]
if mode=='read':controller.read(label)
elif mode=='start':
    if label not in ('rehearse','apply','verify'):raise ValueError('Unknown restoration mode')
    originals={digest:(ROOT/'.work/alpha-t-onboarding-completion-20261009/originals'/(digest+'.xml')).read_bytes() for digest,_ in SOURCES.values()}
    parse_sources(originals)
    packed=base64.b64encode(zlib.compress(json.dumps({k:base64.b64encode(zlib.compress(v)).decode() for k,v in originals.items()}).encode())).decode()
    chunks=[packed[i:i+50000] for i in range(0,len(packed),50000)]
    script='restore_t_receivable_evidence_20261009.py'
    code_hash=hashlib.sha256((ROOT/'scripts'/script).read_bytes()).hexdigest()
    env={'IBKR_RUN_MIGRATIONS':'false','T_EVIDENCE_MODE':label,'T_EVIDENCE_CODE_SHA':code_hash,
        'T_EVIDENCE_PARTS':str(len(chunks)),**{'T_EVIDENCE_PART_'+str(i):v for i,v in enumerate(chunks)}}
    if label in ('apply','verify'):
        prior=json.loads((controller.FOLDER/(('rehearse' if label=='apply' else 'apply')+'-result.json')).read_bytes())
        if prior['status']!='Succeeded' or prior['job_settings_unchanged'] is not True:raise ValueError('Prerequisite execution is not qualified')
        records=[r for r in prior['records'] if r.get('passed')]
        if len(records)!=1:raise ValueError('Prerequisite receipt missing or ambiguous')
        env['T_EVIDENCE_PRIOR_SHA']=records[0]['sha256']
    controller.start(label,'ibkrv2stg-onboard-render',script,env)
else:raise ValueError('Expected start or read')
