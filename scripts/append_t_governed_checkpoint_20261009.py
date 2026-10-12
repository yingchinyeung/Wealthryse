"""Append owner-workflow and independent readback evidence; no Azure writes."""
import hashlib
import html
import json
from datetime import datetime, timezone
from pathlib import Path
from audit_azure_runtime import az

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output/audit/AlphaWorld_T_Onboarding_20261009'


def main():
    path=ROOT/'.work/alpha-t-live-onboarding-20261009-v1/readiness-result.json'
    receipt=json.loads(path.read_bytes())
    assert receipt['status']=='Succeeded' and receipt['job_settings_unchanged'] and len(receipt['records'])==1
    live=receipt['records'][0]
    assert live['read_only'] and live['persistent_writes']==0
    assert live['definition'][0]['configuration_sha256']=='99b9d84e8118632b59dfd73fa64c08c05af409bb05a3daa34dd2fe78173f0209'
    assert live['definition'][0]['approval_status']=='approved'
    checks={r['check_id']:r for r in live['checks']}
    assert checks['data_import_reconciled']['status']=='pass'
    execution=az('containerapp','job','execution','list','-g','rg-ibkr-database-v11',
        '-n','ibkrv2stg-derived-daily','--query',
        "[?name=='ibkrv2stg-derived-daily-8633hfv'].{name:name,status:properties.status,start:properties.startTime,end:properties.endTime}")
    assert len(execution)==1 and execution[0]['status']=='Succeeded'
    screenshot=OUT/'HKD_Contract_Approved.png'
    result={'generated_at':datetime.now(timezone.utc).isoformat(),
        'status':'Runtime deployed and HKD history reconciled; onboarding remains incomplete',
        'supersedes_status_in':'Checkpoint_v6.json; previous evidence retained',
        'deployment_evidence':'Checkpoint_v6.json',
        'governed_website_actions':['Exact HKD calculation contract approved using delegated owner authority.',
            'HSI primary and GSPC secondary registered in HKD.',
            'iStrategy: Alpha World T identity and platform-standard layout locked.',
            'Normal deterministic analytics job dispatched once.'],
        'execution':execution[0],'independent_database_readback':live,
        'report_document_upload':{'status':'blocked_before_upload',
            'reason':'ChatGPT Chrome extension requires Allow access to file URLs.',
            'permission_changed_by_agent':False,'files_uploaded':0},
        'source_broker_records_edited':False,'report_approvals_changed':False,
        'T_viewer_access_activated':False,
        'remaining':['Complete T-only risk and HKD comparison acceptance; current risk gate remains pending.',
            'Submit and validate the mandatory three-document package after upload permission is available.',
            'Complete all-month visual acceptance, register reports, verify website downloads and report approvals.',
            'Approve the qualified private preview and activate T; verify existing four accounts remain accessible.'],
        'evidence':[{'file':p.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
                    for p in (path,OUT/'Checkpoint_v6.json',screenshot)]}
    with (OUT/'Checkpoint_v7.json').open('x',encoding='utf-8') as f:json.dump(result,f,indent=2)
    body='<h1>Alpha World T: governed HKD history reconciled</h1><p><strong>Onboarding remains incomplete; T is not yet available to viewers.</strong></p>'
    body+='<p>The exact HKD definition and presentation identity are now approved. The normal calculation job succeeded. Independent readback confirms 448 HKD NAV observations, 20 January 2025 through 7 October 2026. The existing four subjects remain active.</p>'
    body+='<p>Document intake is blocked by the Chrome extension file-upload permission. No files were uploaded and no browser permission was changed. The risk-acceptance gate also remains pending; a completed calculation job is not a completed onboarding.</p>'
    body+='<ol>'+''.join('<li>'+html.escape(s)+'</li>' for s in result['remaining'])+'</ol>'
    body+='<details><summary>Evidence</summary><pre>'+html.escape(json.dumps(result,indent=2))+'</pre></details>'
    with (OUT/'Checkpoint_v7.html').open('x',encoding='utf-8') as f:f.write('<!doctype html><meta charset="utf-8"><title>Alpha World T checkpoint 7</title><style>body{max-width:1000px;margin:40px auto;font:16px/1.55 system-ui}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>'+body)
    print(json.dumps({'checkpoint':str(OUT/'Checkpoint_v7.html'),'onboarding_complete':False}))


if __name__=='__main__':main()
