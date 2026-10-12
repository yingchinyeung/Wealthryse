"""Append current T onboarding evidence without cloud or database writes."""
import hashlib,html,json
from pathlib import Path
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output/audit/AlphaWorld_T_Onboarding_20261009'
receipt_path=ROOT/'.work/alpha-t-governed-acceptance-20261009-v1/completed.json'
raw=receipt_path.read_bytes()
assert hashlib.sha256(raw).hexdigest()=='7d02a9cca2138275a4a401ed08acdcf671252820db1fa00094800b0641ac042b'
receipt=json.loads(raw)
assert receipt['status']=='passed' and receipt['risk_gate']=='pass'
result={
 'generated_at':datetime.now(timezone.utc).isoformat(),
 'status':'Steps 1-6 complete; official historical batch failed on design-reference binding; onboarding incomplete',
 'supersedes_status_in':'Checkpoint_v7.json; all prior evidence retained',
 'documents':{'version':'portal-upload-20261009T091044Z','validated_count':3,
    'supporting_pdf_sha256':'c7c3c2f199532cdfaf8c2ecac589186b1493a44d5194b97a05d40ed5a2d34291',
    'completed_example_sha256':'47301f198d19914bee05b1164bcf65382caab015082a570d746fd67363c56079',
    'presentation_sha256':'ecab30c63fe8332d77d4761ce5b7e8a434610e1706bcd3505cbc767c43b2247c'},
 'risk_acceptance':receipt,
 'reconciliation':{'website_step':6,'status':'complete','nav_observations':448,'through':'2026-10-07'},
 'historical_batch':{'execution':'ibkrv2stg-onboard-render-9qt39g9','months':21,'expected_files':42,
    'validated_files':0,'status':'failed','error':'T cannot use the MS or an unrelated completed design authority'},
 'diagnosis':'The qualified T renderer requires original owner PDF hash in both supporting and completed-design slots. The generated September preview was mistakenly supplied as the completed design. A local delivery-contract test passed with the original owner PDF in both slots.',
 'recovery_attempt':{'normal_portal_replacement':'rejected','error':'Documents cannot be replaced at the current onboarding stage',
    'replacement_registered':False,'reason':'Portal exposes replacement but backend and template-intake service allow only template_intake/reconciliation stages, not historical_reporting.'},
 'next':['Controlled recovery back to document validation, preserving failed batch, history and existing approvals.',
    'Rebind the correct original T design-reference pair through normal document validation.',
    'Reconcile and regenerate 21 private factsheets plus 21 presentations; verify financial and visual output and website downloads.',
    'Complete report approval, private preview and explicit activation only after required gates pass.'],
 'reports_released':False,'viewer_access_activated':False,'broker_records_edited':False,
 'evidence':[{'file':p.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
    for p in [receipt_path,OUT/'Documents_Validated.png',OUT/'Document_Recovery_Blocked.png']]
}
with (OUT/'Checkpoint_v8.json').open('x',encoding='utf-8') as f:json.dump(result,f,indent=2)
body='<h1>Alpha World T: document recovery required</h1><p>Steps 1–6 passed. T is not yet active for viewers. The official 42-file batch stopped at the design-reference check; no report approval was recorded.</p><p>The original completed T owner template is required in both PDF reference slots. A generated preview was supplied instead. The normal replacement form then rejected the correction because the case had already reached historical reporting.</p><p>Existing financial and risk approvals, old documents, and failed-batch evidence remain preserved.</p><pre>'+html.escape(json.dumps(result,indent=2))+'</pre>'
with (OUT/'Checkpoint_v8.html').open('x',encoding='utf-8') as f:f.write('<!doctype html><meta charset="utf-8"><title>Alpha World T checkpoint 8</title><style>body{max-width:1000px;margin:40px auto;font:16px/1.55 system-ui}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>'+body)
print(json.dumps({'checkpoint':str(OUT/'Checkpoint_v8.html'),'onboarding_complete':False}))
