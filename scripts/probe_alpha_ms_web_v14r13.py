"""Isolated ASGI qualification of the exact image using read-only live data.

Entra's HTTP gateway itself is not emulated or treated as qualified here. Owner
headers represent the gateway's already-validated claims only inside this job.
The actual application authorization, routes, validators and headers run intact.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

sys.path[:0] = ['/app/src', '/app/scripts']
import psycopg
from psycopg.rows import dict_row
from ibkr_etl import web
from ibkr_etl.monthly_artifacts import factsheet_layout_version
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

SUBJECT = 'alpha-world-ms'
LAYOUT = 'subject-completed-factsheet-model-v14r13-alpha-world-ms-distribution-quarter-ticks'


async def request(path, headers=None):
    sent, complete = [], asyncio.Event()
    delivered = False
    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {'type':'http.request','body':b'','more_body':False}
        await complete.wait()
        return {'type':'http.disconnect'}
    async def send(message):
        sent.append(message)
        if message['type']=='http.response.body' and not message.get('more_body'):
            complete.set()
    scope = {'type':'http','asgi':{'version':'3.0','spec_version':'2.3'},
        'http_version':'1.1','method':'GET','scheme':'https','path':path,
        'raw_path':path.encode(),'query_string':b'', 'root_path':'',
        'headers':[(b'host',b'isolated-ms-qualification.invalid')]+[
            (k.lower().encode(),v.encode()) for k,v in (headers or {}).items()],
        'client':('127.0.0.1',1),'server':('isolated-ms-qualification.invalid',443)}
    await web.app(scope, receive, send)
    start = next(m for m in sent if m['type']=='http.response.start')
    return start['status'],dict((k.decode().lower(),v.decode()) for k,v in start['headers']),b''.join(
        m.get('body',b'') for m in sent if m['type']=='http.response.body')


def main():
    release=json.loads(Path('/app/ALPHA_MS_HKD_RELEASE.json').read_bytes())
    for name,digest in release['installed_files_sha256'].items():
        if hashlib.sha256(Path('/app',name).read_bytes()).hexdigest()!=digest:
            raise ValueError('Installed source integrity differs')
    if factsheet_layout_version(SUBJECT)!=LAYOUT or release['layout_version']!=LAYOUT:
        raise ValueError('Current chart correction missing')
    # Every subsequent application connection is also read-only by web._database.
    os.environ['PGOPTIONS']='-c default_transaction_read_only=on'
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row,
            options='-c default_transaction_read_only=on -c statement_timeout=120000',connect_timeout=25) as c:
        c.execute("SELECT set_config('app.tenant_id',%s,true)",(web.TENANT_ID,))
        owner=c.execute('SELECT entra_object_id,email FROM app_users WHERE tenant_id=%s AND lower(email)=%s AND status=%s',
            (web.TENANT_ID,'anthony.ying@wealthryse.ai','active')).fetchone()
        if not owner:raise ValueError('Registered owner identity unavailable')
        c.rollback()
    claims={'x-ms-client-principal-id':str(owner['entra_object_id']),
            'x-ms-client-principal-name':str(owner['email'])}
    checks=[]
    for label,path,headers,wanted in (
        ('health','/healthz',{},200),
        ('anonymous_denied','/api/onboarding/'+SUBJECT+'/preview',{},403),
        ('unregistered_denied','/api/onboarding/'+SUBJECT+'/preview',
         {'x-ms-client-principal-id':str(uuid.uuid4()),'x-ms-client-principal-name':'unregistered@example.invalid'},403),
        ('owner_preview','/api/onboarding/'+SUBJECT+'/preview',claims,200),
        ('owner_historical_page','/account-onboarding/'+SUBJECT+'/steps/historical-reports',claims,200),
    ):
        status,response_headers,body=asyncio.run(request(path,headers))
        if status!=wanted:raise ValueError(label+' unexpected HTTP status '+str(status))
        for key,value in web.SECURITY_HEADERS.items():
            if response_headers.get(key.lower())!=value:raise ValueError(label+' security header differs')
        if label=='owner_preview':
            payload=json.loads(body)
            if payload['subject']['reporting_currency']!='HKD' or payload['subject']['lifecycle_status']!='onboarding':
                raise ValueError('Owner preview currency or lifecycle differs')
            history=payload['historical_reports']
            if history['validation']['ready_for_owner_approval']:
                raise ValueError('Legacy batch approval was incorrectly unlocked')
            checks.append({'check':'legacy_batch_still_blocked','status':'pass'})
        if label=='owner_historical_page' and b'Historical monthly report backfill' not in body:
            raise ValueError('Historical report page content missing')
        checks.append({'check':label,'status':'pass','http_status':status})
        print('MS_WEB_STAGE '+json.dumps(checks[-1]),flush=True)
    proof={'status':'passed','image':os.environ['MS_WEB_IMAGE'],'layout_version':LAYOUT,'checks':checks,
        'web_source_sha256':hashlib.sha256(Path('/app/src/ibkr_etl/web.py').read_bytes()).hexdigest(),
        'database_writes':0,'ibkr_source_changed':False,'reports_released':False,'onboarding_activated':False,
        'qualification_type':'isolated exact-image ASGI runtime against live read-only HKD data',
        'gateway_authentication_test':'Not exercised in this isolated job; requires post-cutover authenticated browser check'}
    content=json.dumps(proof,sort_keys=True,indent=2).encode()
    service=BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
    blob=service.get_blob_client('internal-reports',os.environ['MS_WEB_STAGE_BLOB'])
    blob.upload_blob(content,overwrite=False,content_settings=ContentSettings(content_type='application/json'))
    if blob.download_blob().readall()!=content:raise ValueError('Stage proof readback differs')
    print('MS_WEB_STAGE_RESULT '+json.dumps({'status':'passed','checks':len(checks)}),flush=True)


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('MS_WEB_STAGE_RESULT '+json.dumps({'status':'failed','error_class':type(error).__name__,
            'error':str(error) if isinstance(error,ValueError) else 'Service details redacted'}),flush=True)
        raise SystemExit(1)
