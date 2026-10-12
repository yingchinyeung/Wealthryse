"""Pull only immutable OCI source layers, never execute their contents."""
import hashlib,io,json,tarfile
from pathlib import Path
from urllib.parse import urlsplit
import requests
from audit_azure_runtime import az

ROOT=Path(__file__).resolve().parents[1]/'.work/platform-corrections-20261009-v1/changed-sources'
REGISTRY='ibkrv2stgauhecwc4itaoo.azurecr.io'
TARGETS={
 'sync':('ibkr-etl','sha256:39f73f592d98432b1c1912f57008c2171a591b3c851468ebd20fd97e4a8bdb81','app/src/ibkr_etl/pilot_import.py'),
 'worker':('ibkr-platform','sha256:af15770889dc589e493e496cc92334c2296f16ae1bc9f7869d2a2eb50852f1a9','app/scripts/generate_month_end_package.py')}
def sha(data):return hashlib.sha256(data).hexdigest()
def capture(kind):
    repo,digest,wanted=TARGETS[kind];folder=ROOT/kind
    if folder.exists():raise ValueError('Preserve existing capture')
    token=az('acr','login','-n',REGISTRY.split('.')[0],'--expose-token')['accessToken']
    response=requests.post('https://'+REGISTRY+'/oauth2/token',data={'service':REGISTRY,'scope':'repository:'+repo+':pull','grant_type':'refresh_token','refresh_token':token},timeout=30,allow_redirects=False)
    token=None
    if response.status_code!=200:raise ValueError('Pull-only token exchange failed')
    headers={'Authorization':'Bearer '+response.json()['access_token'],'Accept':'application/vnd.oci.image.manifest.v1+json,application/vnd.docker.distribution.manifest.v2+json'}
    def get(kind,digest,size=None):
        r=requests.get('https://'+REGISTRY+'/v2/'+repo+'/'+kind+'/'+digest,headers=headers,timeout=90,allow_redirects=False,stream=True)
        if r.status_code==307:
            location=r.headers['Location'];parsed=urlsplit(location)
            if parsed.scheme!='https' or not parsed.hostname.endswith('.blob.core.windows.net') or parsed.username or parsed.password:raise ValueError('Unexpected blob host')
            r=requests.get(location,timeout=90,allow_redirects=False,stream=True)
        if r.status_code!=200:raise ValueError('Registry read failed')
        result=bytearray()
        for chunk in r.iter_content(1024*1024):
            result.extend(chunk)
            if len(result)>256*1024**2:raise ValueError('Source layer exceeds bound')
        content=bytes(result)
        if 'sha256:'+sha(content)!=digest or (size is not None and len(content)!=size):raise ValueError('Content digest or size differs')
        return content
    manifest=json.loads(get('manifests',digest));proof=[]
    for layer in reversed(manifest['layers']):
        if layer['size']>256*1024**2:raise ValueError('Required source not found in bounded overlay')
        content=get('blobs',layer['digest'],layer['size']);proof.append(layer['digest'])
        with tarfile.open(fileobj=io.BytesIO(content),mode='r:*') as archive:
            for entry in archive:
                name=entry.name.removeprefix('./').removeprefix('/')
                if name==wanted:
                    if not entry.isfile() or entry.size>2*1024**2:raise ValueError('Unexpected source member')
                    data=archive.extractfile(entry).read();folder.mkdir(parents=True)
                    (folder/Path(wanted).name).write_bytes(data)
                    result={'image':REGISTRY+'/'+repo+'@'+digest,'source':wanted,'sha256':sha(data),'verified_layer_digests':proof,'executed':False}
                    (folder/'receipt.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
                    headers.clear();print(json.dumps(result));return
                if '/.wh.' in name:raise ValueError('Whiteout encountered; full overlay evaluation required')
    raise ValueError('Source not found')
if __name__=='__main__':
    import sys
    try:capture(sys.argv[1])
    except requests.RequestException as e:raise SystemExit('Registry read failed: '+type(e).__name__) from None
