"""Resolve the single out-of-production credential target; do not start or rotate it."""
import json
from datetime import datetime,timezone
from azure.identity import AzureCliCredential
import requests
import review_upgrade_credential_groups_20261011 as c
from deploy_viewer_private_admin_20261008 import request,rid

HOST='ibkrv2stg-ownertrial-20261004.postgres.database.azure.com'
NAME='ownertrial-20261004-admin-url';VAULT='ibkrv2stg-auhecwc4itaoo'
OUT=c.ROOT/'.work/platform-upgrade-20261011-stage31/credential-groups-v2'

def main():
    if OUT.exists():raise ValueError('Preserve existing classification')
    prior=c.protected(c.OUT/'result.json')
    unresolved=prior['unsupported_connection_entries']
    if len(unresolved)!=1 or unresolved[0]['secret']!=VAULT+'/'+NAME or unresolved[0]['azure_postgres_host']!=HOST or prior['unavailable'] or prior['unmatched_password_entries']:
        raise ValueError('Different unresolved credential set')
    path=rid('Microsoft.DBforPostgreSQL/flexibleServers','ibkrv2stg-ownertrial-20261004')+'?api-version=2024-08-01'
    server=request('GET',path)
    if server['properties']['state']!='Stopped' or server['properties']['network']['publicNetworkAccess']!='Disabled':raise ValueError('Stopped private trial expected')
    before=c.bindings(c.current_resources());references=[]
    for item in c.current_resources():
        for secret in item['properties']['configuration'].get('secrets') or []:
            if secret.get('keyVaultUrl','').split('/secrets/')[-1].split('/')[0]==NAME:references.append(item['name'])
    if references:raise ValueError('Trial secret has active configuration references')
    with AzureCliCredential() as credential:
        token=credential.get_token('https://vault.azure.net/.default').token
        response=requests.get('https://'+VAULT+'.vault.azure.net/secrets/'+NAME+'?api-version=7.4',headers={'Authorization':'Bearer '+token},timeout=30,allow_redirects=False)
        del token
        if response.status_code!=200:raise ValueError('Trial credential unavailable; no permissions changed')
        secret=response.json();response.close()
        c.HOSTS={HOST}
        identity,password=c.parse_connection(secret['value'])
        del password
        version=secret['id'].rsplit('/',1)[-1];del secret
    if c.bindings(c.current_resources())!=before or request('GET',path)['properties']['state']!='Stopped':raise ValueError('State drift')
    host,port,database,role=identity
    record={'captured_at':datetime.now(timezone.utc).isoformat(),'previous_review':prior['protected_receipt'],
            'production_groups':prior['groups'],'production_groups_consistent':all(g['connection_passwords_consistent'] for g in prior['groups']),
            'retained_trial':{'host':host,'port':port,'database':database,'role':role,'secret':VAULT+'/'+NAME,'version':version,
                              'server_state':'Stopped','public_access':'Disabled','container_configuration_references':references,'credential_retired':False},
            'all_16_database_secret_targets_classified':True,'four_password_aliases_matched':True,
            'database_authentication_tested':False,'last_rotation_verified':False,'credentials_changed':False,
            'secret_values_retained':False,'secret_hashes_retained':False,'trial_server_started':False,'rotation_complete':False,'full_upgrade_complete':False}
    c.save(OUT,'result',record)
    print(json.dumps({'production_role_groups':len(prior['groups']),'all_16_database_secret_targets_classified':True,'retained_stopped_private_trial':True,'credentials_changed':False}))

if __name__=='__main__':main()
