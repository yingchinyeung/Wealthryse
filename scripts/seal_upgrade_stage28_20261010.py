"""Seal completed checks, with remaining gates explicitly not passed."""
import hashlib,json
from datetime import datetime,timezone
from pathlib import Path
import control_upgrade_legacy_dns_20261010 as dns
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261010-stage28'
DOWNLOADS=Path('C:/Users/antho/Downloads')
FILES={
 'alpha-world-ms':('AlphaWorld_MS_Factsheet_2026-09-30.pdf','AlphaWorld_MS_Presentation_2026-09-30.pptx',1047,1026),
 'alpha-world-t':('AlphaWorld_T_Factsheet_2026-09-30.pdf','AlphaWorld_T_Presentation_2026-09-30.pptx',1110,1089),
 'U16088571':('U16088571_Factsheet_2026-09-30 (1).pdf','U16088571_Monthly_Presentation_2026-09-30 (1).pptx',706,707),
 'U16088606':('U16088606_Factsheet_2026-09-30 (1).pdf','U16088606_Monthly_Presentation_2026-09-30 (1).pptx',837,709),
 'hong-kong-china':('hong-kong-china_Factsheet_2026-09-30 (1).pdf','hong-kong-china_Monthly_Presentation_2026-09-30 (1).pptx',836,705)}


def main():
    previous=protected(ROOT/'.work/platform-upgrade-20261010-stage27/closeout.json')
    plan=protected(dns.OUT/'plan.json')
    if dns.source_hash()!=plan['source_hashes'] or dns.boundary()!=plan['boundary']:
        raise ValueError('Current rehearsal boundary changed')
    dns.owned_links(False)
    evidence={name:protected(OUT/path) for name,path in {
        'vault_dns':'vault-dns-v1/verified.json',
        'private_archive':'archive-transport-v1/result.json',
        'private_dns':'legacy-dns-v1/private-probe-v1/result.json',
        'rollback_settings':'legacy-dns-v1/rollback-verified.json',
        'rollback_runtime':'legacy-dns-v1/public-probe-v1/result.json',
        'main_roles':'role-expiry-v1/main/result.json',
        'legacy_roles':'role-expiry-v1/legacy/result.json'}.items()}
    if any(v.get('passed') is not True for v in evidence.values()):
        raise ValueError('Checkpoint incomplete')
    manifest=json.loads((ROOT/'.work/platform-upgrade-20261009-stage3/download-manifest-v1/result.json').read_bytes())
    registry=json.loads(manifest['records'][0]['Result'])['records']
    checked=[]
    for subject,(pdf,ppt,pdfid,pptid) in FILES.items():
        artifacts=next(r for r in registry if r['subject']==subject)['artifacts']
        for kind,name,runid in (('factsheet',pdf,pdfid),('presentation',ppt,pptid)):
            path=DOWNLOADS/name;digest=hashlib.sha256(path.read_bytes()).hexdigest()
            if artifacts[kind]['generation_run_id']!=runid or digest!=artifacts[kind]['output_sha256']:
                raise ValueError('Actual browser download differs from preserved registry manifest')
            checked.append({'subject':subject,'report_type':kind,'run_id':runid,'sha256':digest,'file':str(path)})
    value={'sealed_at':datetime.now(timezone.utc).isoformat(),'passed':True,
        'previous_checkpoint':previous['protected_receipt'],
        'evidence':{k:v['protected_receipt'] for k,v in evidence.items()},
        'legacy_dns_rehearsal_passed':True,'legacy_dns_rollback_passed':True,
        'legacy_rehearsal_links_remaining':0,'workloads_preserved':37,
        'public_access_restricted':False,'main_dns_linked':False,
        'database_role_metadata_checked':16,'expired_database_login_roles':0,
        'actual_credential_last_rotation_verified':False,
        'external_credential_consumers':{'owner_answer':'no','confirmed_on':'2026-10-10',
            'scope':'spreadsheets, local scripts or services outside this resource group using database, storage, registry or IBKR credentials'},
        'owner_browser_acceptance':{'account_cards':5,'september_report_downloads':checked,
            'comparison':'Current owner review route IDs and bytes match preserved 9 October registry manifest',
            'model_cash':{'MS':{'units':58,'nav_share_percent':47.29},'T':{'units':58,'nav_share_percent':34.43}},
            'approvals_changed':False,'viewer_acceptance':False},
        'new_display_finding':'U16088571 physical cash page renders source category BASE_SUMMARY as a currency; display-only correction pending, amounts unchanged',
        'remaining_gates':[
            'Private DNS final promotion; all-caller platform-secret resolution, fresh registry pulls and external-negative checks before restricting public access',
            'Credential ownership, grouped rotation and provider expiry review; no invented last-rotation metadata',
            'Isolated fresh generation, governed regeneration, upload-failure, concurrency and recovery acceptance',
            'Complete website acceptance and physical cash-summary display correction',
            'Genuine scheduled cycle and 14-day observation'],
        'observation_14_day':'UNVERIFIED - actual window not completed',
        'real_viewer_sign_in':'UNVERIFIED - no actual viewer sign-in',
        'human_alert_receipt':'UNVERIFIED',
        'full_upgrade_complete':False,'reports_changed':False,'financial_data_changed':False}
    save(OUT,'closeout',value)
    print(json.dumps({'checkpoint_passed':True,'downloads_verified':len(checked),'full_upgrade_complete':False}))


if __name__=='__main__':main()
