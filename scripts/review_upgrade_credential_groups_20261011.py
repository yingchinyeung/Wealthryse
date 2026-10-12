"""Inspect existing DB secret coupling in memory; retain names and booleans only.

No broker token read, secret values/hashes/lengths logged, credential mutation,
or access grant. An unavailable secret stops qualification, not authorization.
"""
import json,re
import requests
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit,unquote,parse_qs
from azure.identity import AzureCliCredential
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
from retire_upgrade_additional_diagnostics_20261010 import current_resources,bindings

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'.work/platform-upgrade-20261011-stage31/credential-groups-v1'
HOSTS={'ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com','ibkrv11-pg-5rcc62xxqblay.postgres.database.azure.com'}
VAULTS={'ibkrv2stg-auhecwc4itaoo','ibkrv11-5rcc62xxqblay'}

def parse_connection(value):
    uri=urlsplit(value)
    if uri.scheme not in ('postgres','postgresql') or uri.hostname not in HOSTS or uri.fragment or not uri.password or not uri.username:
        raise ValueError('Unsupported database connection shape; value omitted')
    if set(parse_qs(uri.query)) & {'host','hostaddr','service','user','password','dbname','port'}:
        raise ValueError('Redirecting connection options require separate review')
    role=unquote(uri.username);database=unquote(uri.path.lstrip('/'))
    if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]{0,62}',role) or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]{0,62}',database):
        raise ValueError('Unexpected role/database metadata')
    return (uri.hostname,uri.port or 5432,database,role),unquote(uri.password)

def summarize(secrets):
    groups={};passwords={};unsupported=[]
    for key,value in secrets.items():
        if key[1].endswith('-url'):
            try:identity,password=parse_connection(value)
            except ValueError:
                uri=urlsplit(value)
                host=uri.hostname or ''
                unsupported.append({'secret':'/'.join(key),'azure_postgres_host':host if re.fullmatch(r'[a-z0-9-]+\.postgres\.database\.azure\.com',host) else 'not_expected_azure_hostname',
                                    'postgres_scheme':uri.scheme in ('postgres','postgresql'),'username_present':bool(uri.username),'password_present':bool(uri.password),
                                    'requires_review':True})
                continue
            group=groups.setdefault(identity,{'names':[],'password':password,'consistent':True})
            group['names'].append('/'.join(key));group['consistent'] &= group['password']==password
        elif key[1].endswith('-password'):passwords['/'.join(key)]=value
        else:raise ValueError('Non-database secret forbidden')
    result=[]
    for identity,group in groups.items():
        host,port,database,role=identity
        result.append({'host':host,'port':port,'database':database,'role':role,'connection_secrets':sorted(group['names']),
                       'connection_passwords_consistent':group['consistent'],
                       'matching_password_secrets':sorted(n for n,v in passwords.items() if v==group['password'])})
    unmatched=sorted(n for n,v in passwords.items() if not any(v==g['password'] for g in groups.values()))
    return sorted(result,key=lambda g:(g['host'],g['role'],g['database'])),unmatched,unsupported

def main():
    if OUT.exists():raise ValueError('Preserve earlier review')
    baseline=protected(ROOT/'.work/platform-upgrade-20261011-stage31/resume-v1/result.json')
    before=bindings(current_resources())
    if before!=baseline['bindings']:raise ValueError('Workload drift')
    old=protected(ROOT/'.work/platform-upgrade-20261010-stage23/credential-consumers-v1/review.json')
    targets=[(r['vault'],r['name']) for r in old['secrets'] if r['classification_inferred_from_name_only'] in ('database_connection','database_password')]
    if len(targets)!=16 or any(v not in VAULTS or not n.endswith(('-url','-password')) for v,n in targets):raise ValueError('Exact sixteen DB records required')
    credentials=AzureCliCredential();values={};metadata=[];denied=[]
    try:
        token=credentials.get_token('https://vault.azure.net/.default').token
        for vault in sorted(VAULTS):
            for v,name in targets:
                if v!=vault:continue
                response=requests.get('https://'+vault+'.vault.azure.net/secrets/'+name+'?api-version=7.4',
                    headers={'Authorization':'Bearer '+token},timeout=30,allow_redirects=False)
                if response.status_code!=200:
                    denied.append({'vault':vault,'name':name,'http_status':response.status_code});response.close();continue
                secret=response.json();response.close()
                if not isinstance(secret.get('value'),str) or not secret['value']:raise ValueError('Invalid credential; value omitted')
                values[(vault,name)]=secret['value'];attributes=secret.get('attributes',{})
                metadata.append({'vault':vault,'name':name,'version':secret['id'].rsplit('/',1)[-1],
                                 'enabled':attributes.get('enabled'),'created':attributes.get('created'),'expires':attributes.get('exp')})
                del secret
        del token
        groups,unmatched,unsupported=summarize(values)
    finally:
        values.clear();credentials.close()
    if bindings(current_resources())!=before:raise ValueError('Workloads changed during review')
    record={'captured_at':datetime.now(timezone.utc).isoformat(),'database_secret_records_reviewed':len(metadata),
            'groups':groups,'unmatched_password_entries':unmatched,'unsupported_connection_entries':unsupported,'metadata':metadata,'unavailable':denied,
            'coupling_review_complete':not denied and not unmatched and not unsupported and all(g['connection_passwords_consistent'] for g in groups),
            'broker_token_read':False,'secret_values_retained':False,'secret_hashes_retained':False,
            'credentials_changed':False,'access_grants_added':False,'database_connections_tested':False,
            'actual_last_rotation_verified':False,'external_consumers_absent_owner_confirmed':True,
            'rotation_complete':False,'full_upgrade_complete':False}
    save(OUT,'result',record)
    print(json.dumps({k:record[k] for k in ('database_secret_records_reviewed','groups','unmatched_password_entries','unsupported_connection_entries','unavailable','coupling_review_complete','rotation_complete')}))

if __name__=='__main__':main()
