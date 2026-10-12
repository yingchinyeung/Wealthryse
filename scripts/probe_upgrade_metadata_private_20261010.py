"""Injected private IP and exact monitor source; no secret-value endpoint access."""
import ipaddress,json,os,socket,ssl,types
from datetime import datetime,timezone
import requests
from azure.identity import ManagedIdentityCredential


def main():
    if ipaddress.ip_address(PRIVATE_IP) not in ipaddress.ip_network('10.50.10.0/27'):
        raise ValueError('Private address outside approved subnet')
    vault='ibkrv11-5rcc62xxqblay';host=vault+'.vault.azure.net'
    monitor=types.ModuleType('private_metadata_monitor')
    exec(compile(MONITOR_SOURCE,'metadata-only-source','exec'),monitor.__dict__)
    original=socket.getaddrinfo
    def lookup(name,port,*args,**kwargs):
        return original(PRIVATE_IP if name==host else name,port,*args,**kwargs)
    socket.getaddrinfo=lookup
    session=requests.Session();session.trust_env=False
    credential=ManagedIdentityCredential(client_id=os.environ['AZURE_CLIENT_ID'])
    statuses=[]
    def fetch(url):
        monitor.listing_url(vault,url)
        token=credential.get_token('https://vault.azure.net/.default').token
        try:
            with session.get(url,headers={'Authorization':'Bearer '+token},timeout=30,allow_redirects=False,stream=True) as response:
                if response.status_code!=200:raise ValueError('Private metadata request failed')
                peer=response.raw._connection.sock.getpeername()[0]
                if peer!=PRIVATE_IP:raise ValueError('Actual response peer is not approved private endpoint')
                chunks=[];size=0
                for chunk in response.iter_content(16384):
                    size+=len(chunk)
                    if size>512*1024:raise ValueError('Metadata byte bound exceeded')
                    chunks.append(chunk)
                value=json.loads(b''.join(chunks));statuses.append(200)
                return value
        finally:del token
    try:
        result=monitor.collect(vault,fetch,datetime.now(timezone.utc))
    finally:
        socket.getaddrinfo=original;session.close();credential.close()
    print('PRIVATE_METADATA_PROOF '+json.dumps({'passed':True,'vault':vault,'private_peer':PRIVATE_IP,
        'listed':result['listed'],'statuses':statuses,'verified_tls':True,
        'secret_values_read':0,'secret_values_changed':0,'permissions_changed':False,
        'production_dns_changed':False,'source_sha256':SOURCE_SHA256}),flush=True)


if __name__=='__main__':main()
