"""Freeze and dispatch exactly one isolated HIBOR rehearsal; no promotion."""
import base64
import hashlib
import json
import re
from pathlib import Path
import sys
import zlib

import run_alpha_ms_recovery_operation as operation

ROOT=Path(__file__).resolve().parents[1]
revision=sys.argv[2] if len(sys.argv)>2 else None
if revision is not None and not re.fullmatch(r'r[1-9][0-9]?',revision):
    raise ValueError('Revision must be r1 through r99')
FOLDER=ROOT/('.work/alpha-ms-hibor-step1-20261007'+('-'+revision if revision else ''))
operation.FOLDER=FOLDER


def start():
    FOLDER.mkdir(exist_ok=False)
    source={name:(ROOT/'src/ibkr_etl'/(name+'.py')).read_text(encoding='utf-8')
        for name in ('market_calendar','hibor')}
    content=json.dumps(source,sort_keys=True,separators=(',',':')).encode()
    digest=hashlib.sha256(content).hexdigest()
    (FOLDER/'frozen-code.json').open('xb').write(content)
    operation.start('isolated-feed','ibkrv2stg-onboard-render','qualify_alpha_ms_hibor_feed.py',{
        'ALPHA_MS_HIBOR_CODE_BUNDLE':base64.b64encode(zlib.compress(content)).decode(),
        'ALPHA_MS_HIBOR_CODE_SHA256':digest,'HIBOR_LICENSE_SCOPE_CONFIRMED':'true',
        'DATABASE_URL':'','IBKR_RUN_MIGRATIONS':'false'})


def evidence():
    """Read back the exact frozen revision's private evidence, not another run."""
    from azure.core.credentials import AccessToken
    from azure.storage.blob import BlobServiceClient
    class StorageCredential:
        def get_token(self,*scopes,**kwargs):
            token=operation.az('account','get-access-token','--resource','https://storage.azure.com/')
            return AccessToken(token['accessToken'],int(token['expires_on']))
    digest=hashlib.sha256((FOLDER/'frozen-code.json').read_bytes()).hexdigest()
    container=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',
        credential=StorageCredential()).get_container_client('internal-reports')
    matches=[]
    for blob in container.list_blobs(name_starts_with='review/alpha-world-ms/hibor-step1-20261007/qualification-'):
        content=container.download_blob(blob.name).readall()
        receipt=json.loads(content)
        if receipt['code_bundle_sha256']!=digest:continue
        evidence_sha=hashlib.sha256(content).hexdigest()
        if not blob.name.endswith(evidence_sha+'.json') or receipt['status']!='qualified':
            raise ValueError('Private qualification evidence integrity failed')
        target=FOLDER/'verified-evidence.json'
        if target.exists():
            if target.read_bytes()!=content:raise ValueError('Existing evidence differs')
        else:target.open('xb').write(content)
        matches.append({'blob':blob.name,'sha256':evidence_sha,
            'historical_average_unchanged':receipt['historical_average_unchanged'],
            'retained_provenance':receipt['retained_provenance'],
            'observations_loaded':receipt['refresh_result']['observations_loaded'],
            'validated_period_observations':receipt['refresh_result']['validated_period_observations']})
    if len(matches)!=1:raise ValueError('Exactly one matching frozen qualification required')
    print(json.dumps(matches[0]))


if __name__=='__main__':
    if sys.argv[1]=='start':start()
    elif sys.argv[1]=='read':operation.read('isolated-feed')
    elif sys.argv[1]=='evidence':evidence()
    else:raise ValueError('Only start/read/evidence allowed; this controller cannot promote')
