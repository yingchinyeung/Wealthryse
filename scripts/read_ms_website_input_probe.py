"""Versioned readback: normalize only Azure's empty secret-bound env defaults."""
import copy
import hashlib
import json
from pathlib import Path
import re

from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
import run_ms_website_input_probe as probe


def normalized_env(entries):
    result=copy.deepcopy(entries)
    if len({e['name'] for e in result}) != len(result):
        raise ValueError('Duplicate environment names')
    for e in result:
        if e.get('secretRef') and e.get('value') == '': e.pop('value')
    return sorted(result, key=lambda e:e['name'])


def verify_env(expected, actual, job):
    wanted=normalized_env(expected); observed=normalized_env(actual)
    if len(wanted) != len(observed): raise ValueError('Environment count differs')
    alias_used=False
    for old,new in zip(wanted,observed):
        if old.get('secretRef') != new.get('secretRef'):
            db=[e for e in job['properties']['template']['containers'][0]['env'] if e['name']=='DATABASE_URL']
            secrets=[s for s in job['properties']['configuration']['secrets'] if s['name']=='database-url']
            identities={k.casefold() for k in job.get('identity',{}).get('userAssignedIdentities',{})}
            if (old['name'] != 'DATABASE_URL' or new['name'] != 'DATABASE_URL'
                    or old.get('secretRef') != 'database-url' or new.get('secretRef') != 'cappjob-'+job['name']
                    or len(db) != 1 or db[0].get('secretRef') != old['secretRef']
                    or len(secrets) != 1 or secrets[0].get('value')
                    or secrets[0].get('keyVaultUrl') != 'https://ibkrv2stg-auhecwc4itaoo.vault.azure.net/secrets/database-app-url'
                    or str(secrets[0].get('identity') or '').casefold() not in identities):
                raise ValueError('Unverified Azure execution secret alias')
            new['secretRef']=old['secretRef']; alias_used=True
        if old != new: raise ValueError('Executed environment changed')
    return alias_used


def main():
    intent=probe.release.read(probe.FOLDER,'intent'); probe.verify_protected(intent)
    if (intent['controller_sha256'] != probe.sha(Path(probe.__file__))
            or intent['source_sha256'] != probe.sha(probe.ROOT/'scripts/probe_ms_website_comparison_inputs.py')):
        raise ValueError('Frozen original source changed')
    job=probe.request('GET',probe.release.path(probe.JOB)+probe.release.API)
    probe.release.scope(job,probe.JOB)
    if (job['properties']['template']['containers'][0]['image'] != probe.IMAGE
            or probe.release.settings_invariant(job) != intent['job_settings_sha256']):
        raise ValueError('Persistent worker changed')
    execution=probe.release.read(probe.FOLDER,'dispatch')['execution']
    if not re.fullmatch(re.escape(probe.JOB)+'-[a-z0-9]{7}',execution): raise ValueError('Unexpected execution')
    run=probe.request('GET',probe.release.path(probe.JOB)+'/executions/'+execution+probe.release.API)
    print(json.dumps({'status':run['properties']['status'],'execution':execution}),flush=True)
    if run['properties']['status'] != 'Succeeded': return
    actual=run['properties']['template']['containers']; expected=intent['template']['containers']
    if len(actual) != len(expected) or len(actual) != 1:
        raise ValueError('Unexpected container count')
    if any(actual[0].get(k) != expected[0].get(k) for k in ('image','command','args','name')):
        raise ValueError('Executed source, image or environment changed')
    alias_used=verify_env(expected[0]['env'],actual[0]['env'],job)
    service=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=AzureCliCredential())
    blob=service.get_blob_client('internal-reports',probe.PREFIX+'inputs.json')
    content=blob.download_blob().readall(); digest=hashlib.sha256(content).hexdigest()
    if blob.get_blob_properties().metadata.get('sha256') != digest: raise ValueError('Input integrity differs')
    value=json.loads(content)
    if (value['status'] != 'read_only_inputs_captured' or value['database_writes'] != 0
            or any(value[k] is not False for k in ('ibkr_source_changed','factsheets_changed','reports_released','onboarding_activated'))):
        raise ValueError('Probe scope differs')
    proof={'status':'read_only_inputs_verified','execution':execution,'inputs_sha256':digest,
           'source_sha256':intent['source_sha256'],'persistent_settings_preserved':True,
           'database_writes':0,'factsheets_changed':False,'onboarding_activated':False,
           'normalization':'Empty secret-bound values and exact job-specific execution database alias; single existing Key Vault reference/identity verified',
           'execution_database_alias_verified':alias_used,
           'reader_sha256':probe.sha(Path(__file__))}
    path=probe.FOLDER/'inputs.json'
    if path.exists():
        if path.read_bytes() != content: raise ValueError('Local evidence differs')
    else:
        with path.open('xb') as f: f.write(content)
    target=probe.FOLDER/'qualified-v3.json'
    if target.exists():
        saved=json.loads(target.read_bytes()); probe.verify_protected(saved)
        if {k:v for k,v in saved.items() if k != 'protected_receipt'} != proof: raise ValueError('Existing qualification differs')
    else:
        probe.release.build.save(probe.FOLDER,'qualified-v3',{
            **proof,'protected_receipt':probe.retain(proof,'ms-website-inputs-v1-readback-v3')})
    print(json.dumps({'status':proof['status'],'inputs_sha256':digest,
                      'nav_rows':len(value['nav']),'price_rows':len(value['prices']),'fx_rows':len(value['fx'])}),flush=True)


if __name__ == '__main__': main()
