"""Find exact historical bytes read-only; copy later using the existing report identity."""
import hashlib,json,sys
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient,ContentSettings
from azure.core.exceptions import ResourceExistsError
from run_specialist_gateway_read_verification import retain

ROOT=Path(__file__).resolve().parents[1]/'.work/platform-upgrade-20261009-stage8/report-version-recovery-v2'
EVIDENCE='review/platform-upgrade-20261009/recovery-source-23ac5ec6e8d7e81b2ce5612ceb864ea0874d1e25c71a6fd8c31069db379c4449.json'
SHA='23ac5ec6e8d7e81b2ce5612ceb864ea0874d1e25c71a6fd8c31069db379c4449'

def main():
    if ROOT.exists():raise ValueError('Preserve earlier recovery operation')
    client=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=AzureCliCredential())
    container=client.get_container_client('internal-reports')
    data=container.get_blob_client(EVIDENCE).download_blob().readall()
    if hashlib.sha256(data).hexdigest()!=SHA:raise ValueError('Diagnostic evidence differs')
    records=json.loads(data)['artifact_mismatches']
    if len(records)!=375 or any(r['status']!='awaiting_approval' or not r['output_reference'].startswith('internal-reports/review/') for r in records):raise ValueError('Unexpected repair scope')
    groups=defaultdict(list)
    for row in records:groups[row['output_reference'].split('/',1)[1]].append(row)
    ROOT.mkdir()
    with (ROOT/'intent.json').open('x',encoding='utf-8') as f:json.dump({'evidence_sha256':SHA,'rows':375,'paths':len(groups),'database_writes':0,'overwrite_current_files':False},f,indent=2)
    def recover(item):
        name,rows=item;wanted={r['output_sha256'] for r in rows};found={};checked=0
        versions=list(container.list_blobs(name_starts_with=name,include=['versions']))
        if len(versions)>200:raise ValueError('Version inventory exceeds bounded scope')
        for version in versions:
            if version.name!=name or not version.version_id:continue
            if version.size>100*1024*1024:raise ValueError('Report size exceeds bound')
            original=container.get_blob_client(name,version_id=version.version_id).download_blob().readall()
            digest=hashlib.sha256(original).hexdigest();checked+=1
            if digest not in wanted or digest in found:continue
            target='recovery/report-integrity-20261009/'+digest+'/'+name.rsplit('/',1)[1]
            found[digest]={'old_reference':'internal-reports/'+name,'source_version':version.version_id,'sha256':digest,
                           'recovered_reference':'internal-reports/'+target,'copy_complete':False,'bytes':len(original)}
            if set(found)==wanted:break
        return {'path':name,'checked_versions':checked,'found':list(found.values()),'missing':sorted(wanted-set(found))}
    results=[]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for value in pool.map(recover,groups.items()):
            results.append(value)
            with (ROOT/'progress.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(value)+'\n')
            print(json.dumps({'paths_checked':len(results),'total_paths':len(groups),'missing_hashes':sum(len(r['missing']) for r in results)}),flush=True)
    lookup={(r['old_reference'],r['sha256']):r for item in results for r in item['found']}
    repairs=[{**r,'recovery':lookup[(r['output_reference'],r['output_sha256'])]} for r in records if (r['output_reference'],r['output_sha256']) in lookup]
    value={'passed':len(repairs)==375,'source_evidence_sha256':SHA,'repairs':repairs,'paths':results,'database_writes':0,'original_files_overwritten':False,'released_files_changed':False,'copy_complete':False}
    receipt=retain(value,'upgrade-historical-report-byte-recovery')
    with (ROOT/'result.json').open('x',encoding='utf-8') as f:json.dump({**value,'protected_receipt':receipt},f,indent=2)
    print(json.dumps({'qualified_repairs':len(repairs),'unresolved':375-len(repairs),'registry_changed':False,'protected_readback_verified':receipt['protected_readback_verified']}),flush=True)
if __name__=='__main__':main()
