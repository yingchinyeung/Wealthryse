"""Reuse same-turn exact baseline rehearsal only when all recovery dependencies match."""
import json
import run_ms_website_input_probe as p
from qualify_viewer_native_runtime_20261008 import ROOT,CANDIDATE
from deploy_viewer_report_fixes_20261008 import normalized
from repair_alpha_ms_website_batch import web_invariant

def main():
    old_folder=ROOT/'candidate';run_folder=ROOT/'native-rollback';target=ROOT/'native-rollback-v2'
    if target.exists():raise ValueError('Preserve prior recovery evidence')
    old=p.release.read(old_folder,'baseline');new=p.release.read(CANDIDATE,'baseline')
    for value in (old,new):p.verify_protected(value)
    if old['website']!=new['website'] or old['auth']!=new['auth'] or old['image']!=new['image']:raise ValueError('Exact recovery baseline changed')
    current=p.request('GET',new['website']['id']+p.release.API)
    auth=p.request('GET',new['website']['id']+'/authConfigs/current'+p.release.API)
    if current['properties']['template']['containers'][0]['image']!=new['image'] or web_invariant(normalized(current))!=web_invariant(normalized(new['website'])) or auth['properties']!=new['auth']['properties']:raise ValueError('Live recovery dependencies changed')
    old_assets=json.loads((old_folder/'context/MS_WEBSITE_BENCHMARK_RELEASE.json').read_bytes())['approved_report_files_sha256']
    new_assets=json.loads((CANDIDATE/'context/MS_WEBSITE_BENCHMARK_RELEASE.json').read_bytes())['approved_report_files_sha256']
    if old_assets!=new_assets:raise ValueError('Approved reporting dependencies changed')
    proof=p.release.read(run_folder,'qualified');p.verify_protected(proof)
    intent=p.release.read(run_folder,'intent');p.verify_protected(intent)
    if proof['status']!='passed' or proof['image']!=new['image'] or proof['database_writes']!=0 or proof['proof_sha256']!=p.sha(run_folder/'proof.json') or proof['source_sha256']!=intent['source_sha256']:raise ValueError('Exact original rehearsal not verified')
    target.mkdir()
    record={k:v for k,v in proof.items() if k!='protected_receipt'}
    record.update(reused_same_turn_baseline_rehearsal=True,original_qualified_sha256=p.sha(run_folder/'qualified.json'),original_baseline_sha256=p.sha(old_folder/'baseline.json'),current_baseline_sha256=p.sha(CANDIDATE/'baseline.json'),approved_report_hashes_equal=True,live_auth_and_settings_unchanged=True,new_execution_dispatched=False)
    p.release.build.save(target,'qualified',{**record,'protected_receipt':p.retain(record,'viewer-v2-exact-baseline-recovery-rebound')})
    print(json.dumps({'status':'passed','same_exact_baseline_reused':True,'execution':proof['execution'],'new_execution_dispatched':False}))

if __name__=='__main__':main()
