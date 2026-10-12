"""Pure environment verification; no deployment-controller imports."""
import copy


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
                    or old.get('secretRef') != 'database-url' or new.get('secretRef') != 'cappjob-ibkrv2stg-onboard-render'
                    or len(db) != 1 or db[0].get('secretRef') != old['secretRef']
                    or len(secrets) != 1 or secrets[0].get('value')
                    or secrets[0].get('keyVaultUrl') != 'https://ibkrv2stg-auhecwc4itaoo.vault.azure.net/secrets/database-app-url'
                    or str(secrets[0].get('identity') or '').casefold() not in identities):
                raise ValueError('Unverified Azure execution secret alias')
            new['secretRef']=old['secretRef']; alias_used=True
        if old != new: raise ValueError('Executed environment changed')
    return alias_used
