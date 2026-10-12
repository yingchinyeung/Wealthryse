"""Read-only private service requests from an existing workload identity.

Injected TARGETS contain public service URLs/secret references, never values.
Only this Python process overrides resolution. No host or VNet DNS changes.
"""
import json,os,socket,ssl,re
from urllib.parse import urlsplit
import requests
from azure.identity import ManagedIdentityCredential

def main():
    if not 1<=len(TARGETS)<=5:raise ValueError('Bounded private service scope required')
    mapping={}
    for target in TARGETS:
        p=urlsplit(target['url'])
        if p.scheme!='https' or p.username or p.password or p.fragment or p.port not in (None,443):raise ValueError('Unexpected service URL')
        if target['kind']=='secret' and (not p.hostname.endswith('.vault.azure.net') or not p.path.startswith('/secrets/')):raise ValueError('Secret-reference shape differs')
        if target['kind']=='container' and (not p.hostname.endswith('.blob.core.windows.net') or len(p.path.strip('/').split('/'))!=1):raise ValueError('Container path differs')
        if target['kind']=='blob' and (not p.hostname.endswith('.blob.core.windows.net') or len(p.path.strip('/').split('/'))<2 or p.query):raise ValueError('Blob path differs')
        if target['kind'] not in ('secret','container','blob') or not target['private_ip'].startswith(('10.50.10.','10.40.10.')):raise ValueError('Private endpoint scope differs')
        if p.hostname in mapping and mapping[p.hostname]!=target['private_ip']:raise ValueError('Ambiguous private mapping')
        mapping[p.hostname]=target['private_ip']
    original=socket.getaddrinfo
    def private_lookup(host,port,*args,**kwargs):
        return original(mapping.get(host,host),port,*args,**kwargs)
    socket.getaddrinfo=private_lookup
    credential=ManagedIdentityCredential(client_id=os.environ['AZURE_CLIENT_ID'])
    session=requests.Session();session.trust_env=False;results=[]
    try:
        for target in TARGETS:
            host=urlsplit(target['url']).hostname;ip=target['private_ip']
            with socket.create_connection((host,443),timeout=15) as sock:
                with ssl.create_default_context().wrap_socket(sock,server_hostname=host) as secure:
                    if secure.getpeername()[0]!=ip:raise ValueError('Actual TLS peer is not expected private IP')
            audience='https://vault.azure.net/.default' if target['kind']=='secret' else 'https://storage.azure.com/.default'
            token=credential.get_token(audience).token
            response=session.request('GET' if target['kind']=='secret' else 'HEAD',target['url'],headers={'Authorization':'Bearer '+token,'x-ms-version':'2023-11-03'},timeout=20,allow_redirects=False)
            del token
            if response.status_code!=200:
                code=response.headers.get('x-ms-error-code','unknown')
                if not re.fullmatch('[A-Za-z]{1,80}',code):code='unknown'
                raise ValueError('Private '+target['alias']+' returned HTTP '+str(response.status_code)+' '+code)
            if target['kind']=='secret' and not isinstance(response.json().get('value'),str):raise ValueError('Secret access response differs')
            # Never retain response bodies, secret lengths, hashes or values.
            response.close();del response
            results.append({'kind':target['kind'],'host':host,'private_peer':ip,'status':200,'alias':target['alias']})
            print('PRIVATE_SERVICE_PROGRESS '+json.dumps(results[-1]),flush=True)
    finally:
        socket.getaddrinfo=original;session.close();credential.close()
    print('PRIVATE_SERVICE_PROOF '+json.dumps({'passed':True,'results':results,'database_writes':0,'blob_writes':0,'secret_values_retained':False,'production_dns_changed':False,'scope':'Workload-process managed-identity private requests; does not alone prove Container Apps control-plane secret resolution or cold image pull.'}),flush=True)

if __name__=='__main__':main()
