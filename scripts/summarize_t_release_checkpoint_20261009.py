"""Append an evidence-bound, candid onboarding checkpoint; no Azure writes."""
import hashlib,html,json
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output/audit/AlphaWorld_T_Onboarding_20261009'
def read(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 paths=[]
 def evidence(relative):
  p=ROOT/relative;paths.append(p);return read(p)
 def passed(relative):
  r=evidence(relative)
  if r['status']!='Succeeded' or not r['job_settings_unchanged'] or len(r['records'])!=1 or r['records'][0]['status']!='passed':raise ValueError('Incomplete proof: '+relative)
  return r['records'][0]
 copies=[]
 for relative in ('.work/alpha-t-daily-baselines-20261009-v4/analytics-result.json','.work/alpha-t-daily-baselines-20261009-v1/snapshot-result.json'):
  row=passed(relative);copies.append({k:row[k] for k in ('image','files','blob','sha256','database_connected')})
 candidates={}
 for kind,relative in [('web','.work/alpha-t-runtime-release-20261009-v7/web'),('worker','.work/alpha-t-runtime-release-20261009-v7/worker'),('daily','.work/alpha-t-operating-release-20261009-v4/daily'),('benchmark','.work/alpha-t-operating-release-20261009-v4/benchmark')]:
  scan=evidence(relative+'/scan-completed.json');native=evidence(relative+'/native-build-qualified.json');local=evidence(relative+'/local-qualification.json')
  if scan['assessment']['status']!='pass' or scan['assessment']['blocking_findings'] or not native['qualified']:raise ValueError('Candidate failed')
  candidates[kind]={'image':scan['image'],'native_build':native['run'],'local_tests_passed':local['tests_passed'],
   'local_tests_skipped':local.get('tests_skipped',0),'vulnerabilities':scan['assessment']['counts'],'deployed':False}
 runtime=passed('.work/alpha-t-candidate-rehearsal-20261009-v3/runtime-result.json')
 operating={k:passed('.work/alpha-t-operating-release-20261009-v4/'+k+'/postgres-v1/runtime-result.json') for k in ('daily','benchmark')}
 reports=passed('.work/alpha-t-candidate-reports-20261009-v4/reports-result.json')
 download=evidence('.work/alpha-t-candidate-reports-20261009-v4/download-verified.json')
 if download['count']!=42 or download['proof_sha256']!=reports['proof']['sha256']:raise ValueError('Report readback incomplete')
 native_path=ROOT/'.work/alpha-t-candidate-reports-20261009-v4/download/AlphaWorld_T_Presentation_2026-09-30.pptx'
 result={'generated_at':datetime.now(timezone.utc).isoformat(),'status':'Release candidates qualified; onboarding not complete',
  'supersedes_status_in':'Checkpoint_v4.json; previous evidence retained',
  'authorized_source_copy':copies,'candidate_images':candidates,
  'final_worker_database_rehearsal':{'daily_nav_observations':runtime['nav_observations'],'months':runtime['months'],
   'risk_fields_per_month':21,'cache_replay':'unchanged','persistent_database_writes':0,
   'tenant_isolation':runtime['tenant_isolation'],'onboarding_acceptance':runtime['onboarding_acceptance']},
  'operating_database_rehearsals':{k:{'months':r['months'],'daily_nav_observations':r['nav_observations'],
   'persistent_database_writes':r['database_persistent_writes'],'persistent_state_unchanged':r['persistent_state_unchanged'],
   'benchmark_atomicity':r['operating_benchmark']} for k,r in operating.items()},
  'final_azure_reports':{'factsheets':21,'presentations':21,'financial_validation':'passed','private_storage_readback':'passed',
   'local_download_hash_verification':'passed','proof':reports['proof'],'registered':False,'released':False},
  'native_powerpoint_review':{'period_end':'2026-09-30','deck_sha256':sha(native_path),
   'actual_azure_download_opened_without_repair_prompt':True,'slides_inspected_this_phase':[1,5],
   'allocation_footnote_clear_of_table':True,'all_420_slides_individually_reviewed':False},
  'source_data_changed_in_this_phase':False,'factsheet_wording_changed_in_this_phase':False,
  'live_services_updated_in_this_phase':False,'report_approvals_changed':False,'viewer_access_activated':False,
  'limitations':['Crisis security prices remain provisional internal-use inputs, not independently provider-certified.',
   'Source-bound financial and structural validation does not constitute individual visual review of all 420 slides.'],
  'remaining':['Finish startup/fallback verification and scoped runtime deployment.',
   'Approve the exact HKD definition through the governed owner workflow, then persist and reconcile T analytics.',
   'Finish presentation visual acceptance and register the historical report batch.',
   'Complete website download, approval and viewer-access checks before declaring onboarding complete.'],
  'evidence':[{'file':p.relative_to(ROOT).as_posix(),'sha256':sha(p)} for p in paths]}
 with (OUT/'Checkpoint_v5.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2)
 title='Alpha World T onboarding checkpoint 5'
 body='<h1>'+title+'</h1><p><strong>Release candidates qualified. Onboarding is not complete.</strong></p>'
 body+='<p>Authorized installed-source copy completed. Four exact candidate images passed native builds and the vulnerability gate with no critical or high findings.</p>'
 body+='<p>The final worker and operating images passed the 443-observation, 21-month PostgreSQL rehearsals with no persistent writes. The benchmark partial-failure rollback passed.</p>'
 body+='<p>All 21 factsheets and 21 presentations rendered in Azure, passed financial validation and were downloaded with exact hash verification. They remain private and unregistered.</p>'
 body+='<p>The final September PowerPoint opened without repair. Its cover and corrected allocation slide were inspected. This is not an assertion that every slide in all 21 decks has been visually reviewed.</p><h2>Remaining</h2><ol>'
 body+=''.join('<li>'+html.escape(s)+'</li>' for s in result['remaining'])+'</ol><p>Saved jobs, live website, source financial records, report approvals and viewer access are unchanged in this phase.</p>'
 body+='<details><summary>Evidence and limitations</summary><pre>'+html.escape(json.dumps(result,indent=2))+'</pre></details>'
 with (OUT/'Checkpoint_v5.html').open('x',encoding='utf-8') as stream:stream.write('<!doctype html><meta charset="utf-8"><title>'+title+'</title><style>body{max-width:1000px;margin:40px auto;font:16px/1.55 system-ui;color:#172033}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>'+body)
 print(json.dumps({'checkpoint':str(OUT/'Checkpoint_v5.html'),'onboarding_complete':False}))
if __name__=='__main__':main()
