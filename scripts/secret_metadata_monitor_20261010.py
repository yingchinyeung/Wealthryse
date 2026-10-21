"""Metadata-only secret hygiene monitor. Never reads or changes secret values."""
from datetime import datetime,timezone
import hashlib,json,os,re,sys
from urllib.parse import parse_qs,urlsplit

VAULTS=('ibkrv2flexauhecwc4ita','ibkrv2stg-auhecwc4itaoo')
PREFIX='SECRET_METADATA_MONITOR '
SCHEMA='wealthryse-secret-metadata-v1'
VERSION='20261010-v1'


def listing_url(vault,url):
    if vault not in VAULTS:raise ValueError('Unapproved vault')
    p=urlsplit(url);query=parse_qs(p.query,keep_blank_values=True)
    if (p.scheme!='https' or p.netloc!=vault+'.vault.azure.net' or p.username or p.password or p.fragment
        or p.path!='/secrets' or query.get('api-version')!=['7.4'] or set(query)-{'api-version','maxresults','$skiptoken'}
        or any(len(v)!=1 for v in query.values())):raise ValueError('Metadata listing URL outside fixed vault scope')
    if 'maxresults' in query and query['maxresults']!=['25']:raise ValueError('Unexpected page size')
    return url


def assess(vault,items,now):
    if vault not in VAULTS or now.utcoffset() is None or len(items)>500:raise ValueError('Bounded approved vault required')
    seen=set();findings=[];enabled=disabled=0;stamp=now.timestamp()
    for item in items:
        if not isinstance(item,dict) or 'value' in item:raise ValueError('Secret value is forbidden')
        p=urlsplit(item.get('id',''))
        if p.scheme!='https' or p.netloc!=vault+'.vault.azure.net' or p.query or p.fragment or not re.fullmatch('/secrets/[A-Za-z0-9-]{1,127}',p.path):raise ValueError('Invalid metadata secret id')
        if p.path in seen:raise ValueError('Duplicate metadata item')
        seen.add(p.path);attrs=item.get('attributes')
        if not isinstance(attrs,dict) or type(attrs.get('enabled')) is not bool:raise ValueError('Missing enabled-state metadata')
        if not attrs['enabled']:disabled+=1;continue
        enabled+=1;reasons=[]
        times={}
        for key in ('created','updated','exp','nbf'):
            val=attrs.get(key)
            if val is not None and (type(val) is not int or val<0 or val>253402300799):raise ValueError('Invalid timestamp')
            times[key]=val
        if times['created'] is None:reasons.append('creation_time_missing')
        elif times['created']>stamp:reasons.append('creation_time_in_future')
        if times['updated'] is not None and times['updated']>stamp:reasons.append('updated_time_in_future')
        if times['exp'] is None:reasons.append('expiry_metadata_missing')
        elif times['exp']<=stamp:reasons.append('expired')
        elif times['exp']-stamp<=30*86400:reasons.append('expires_within_30_days')
        if times['nbf'] is not None and times['nbf']>stamp:reasons.append('not_yet_valid')
        tags=item.get('tags') or {}
        if not isinstance(tags,dict):raise ValueError('Invalid tag metadata')
        # Never log tag values: callers might have incorrectly put sensitive
        # information there. Only record the presence of ownership/rotation tags.
        if not any(isinstance(tags.get(k),str) and tags[k].strip() for k in ('owner','Owner','credential-owner')):reasons.append('owner_metadata_missing')
        if not any(isinstance(tags.get(k),str) and tags[k].strip() for k in ('rotation-policy','rotation-owner','rotation-mechanism')):reasons.append('rotation_metadata_missing')
        age_days=None if times['created'] is None else max(0,int((stamp-times['created'])//86400))
        if age_days is not None and age_days>=180:reasons.append('age_review_180_days')
        if reasons:findings.append({'secret_name':p.path.rsplit('/',1)[1],'reasons':reasons,'age_days':age_days,
            'expires_at':times['exp'],'ownership_tag_present':'owner_metadata_missing' not in reasons})
    return {'vault':vault,'listed':len(items),'enabled':enabled,'disabled':disabled,'findings':findings}


def collect(vault,fetch,now):
    url='https://'+vault+'.vault.azure.net/secrets?api-version=7.4&maxresults=25';items=[];seen=set()
    for _ in range(20):
        listing_url(vault,url)
        if url in seen:raise ValueError('Pagination cycle')
        seen.add(url);page=fetch(url)
        if not isinstance(page,dict) or not isinstance(page.get('value'),list) or len(page['value'])>25:raise ValueError('Invalid metadata page')
        items.extend(page['value'])
        if len(items)>500:raise ValueError('Metadata inventory exceeds bound')
        url=page.get('nextLink')
        if not url:return assess(vault,items,now)
        if not isinstance(url,str):raise ValueError('Invalid nextLink')
    raise ValueError('Unfinished metadata pagination')


def main():
    from azure.identity import ManagedIdentityCredential
    import requests
    client=os.environ.get('AZURE_CLIENT_ID','')
    if not re.fullmatch('[0-9a-fA-F-]{36}',client):raise ValueError('Explicit dedicated managed identity required')
    credential=ManagedIdentityCredential(client_id=client);now=datetime.now(timezone.utc)
    def fetch(url):
        # collect() validates exact host/path before a token-bearing request.
        token=credential.get_token('https://vault.azure.net/.default').token
        try:response=requests.get(url,headers={'Authorization':'Bearer '+token},timeout=30,allow_redirects=False)
        finally:del token
        if response.status_code!=200:raise RuntimeError('Metadata request failed HTTP '+str(response.status_code))
        if len(response.content)>512*1024:raise ValueError('Metadata page exceeds byte limit')
        return response.json()
    vaults=[];errors=[]
    try:
        for vault in VAULTS:
            try:vaults.append(collect(vault,fetch,now))
            except Exception as exc:errors.append({'vault':vault,'error_type':type(exc).__name__})
    finally:credential.close()
    record={'schema':SCHEMA,'version':VERSION,'checked_at':now.isoformat(),'kind':'complete' if not errors else 'failed',
        'vaults':vaults,'errors':errors,'vault_count':len(vaults),'finding_count':sum(len(v['findings']) for v in vaults),
        'secret_values_read':0,'secret_values_changed':0,'expiry_changes':0,'permissions_changed':0,
        'age_policy':'180 days triggers review only; not expiry or automatic rotation'}
    print(PREFIX+json.dumps(record,sort_keys=True),flush=True)
    if errors:raise SystemExit(1)


if __name__=='__main__':main()
