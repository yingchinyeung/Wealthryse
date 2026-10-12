"""Emit an apply_patch manifest update; preserve all approved report hashes."""
import hashlib,json
from pathlib import Path
root=Path(__file__).resolve().parents[1]/'.work/viewer-report-investigation-20261008/candidate-v2/context'
path=root/'MS_WEBSITE_BENCHMARK_RELEASE.json'
old=path.read_text(encoding='utf-8');value=json.loads(old)
value['scope']='Viewer export, analyst authorization and portal presentation fixes; approved reporting assets unchanged'
for name in ('web','web_theme','metrics_guide','agent_tools','ai_analyst','subject_exports','viewer_labels'):
    relative='src/ibkr_etl/'+name+'.py'
    value['website_files_sha256'][relative]=hashlib.sha256((root/relative).read_bytes()).hexdigest()
value['private_postgres_proof_sha256']=None
value['prior_ms_benchmark_proof_preserved']=True
new=json.dumps(value,indent=2)+'\n'
print('*** Begin Patch\n*** Update File: '+path.as_posix()+'\n@@')
print('\n'.join('-'+l for l in old.splitlines()))
print('\n'.join('+'+l for l in new.splitlines()))
print('*** End Patch')
