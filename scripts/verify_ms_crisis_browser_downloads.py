"""Hash-bind actual signed-in website downloads to accepted Azure files."""
import hashlib,json
from pathlib import Path
import release_ms_crisis as candidate
import build_ms_presentation_worker_candidate as build
from run_specialist_gateway_read_verification import retain

def main():
    folder=candidate.FOLDER.parent/'candidate-v6'
    source=folder/'browser-downloads-v1.json'
    observed=json.loads(source.read_bytes())
    expected=json.loads((folder/'worker/candidate-runtime-v1/verification.json').read_bytes())['artifacts']
    assert len(observed['rows'])==21 and observed['approvals_submitted']==0
    proof={r['period_end']:r for r in expected};rows=[]
    for row in observed['rows']:
        wanted=proof.pop(row['period_end'])
        assert row['review_status']=='No changes selected.' and not row['approval_checkboxes_checked']
        files={}
        for kind in ('factsheet','presentation'):
            data=Path(row[kind+'_path']).read_bytes();h=hashlib.sha256(data).hexdigest()
            assert h==wanted[kind+'_sha256'],'Website download differs: '+row['period_end']+' '+kind
            files[kind]={'sha256':h,'bytes':len(data),'generation_run_id':row[kind+'_run_id']}
        rows.append({'period_end':row['period_end'],'files':files,'review_checklist_visible':True})
    assert not proof
    record={'status':'passed','months':21,'website_downloads_verified':42,'approvals_submitted':0,
        'browser_manifest_sha256':build.sha(source),'rows':rows}
    build.save(folder,'browser-downloads-qualified',{**record,'protected_receipt':retain(record,'ms-crisis-v6-actual-owner-browser-downloads')})
    print(json.dumps({'status':'passed','months':21,'actual_website_downloads':42,'approvals_submitted':0}))

if __name__=='__main__':main()
