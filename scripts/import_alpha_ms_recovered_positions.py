"""Atomic preview/apply of original XML observations; insert missing rows only.

CONTROLS and SOURCES are supplied by the audited local dispatch bundle. Preview
performs the exact inserts and validation in a rollback-only transaction.
"""
import hashlib
import base64
import zlib
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path.cwd() / 'src'))
import psycopg
from ibkr_etl.db import PostgresConnection, _hybrid_row_factory
from ibkr_etl.parser import parse_flex_xml
from ibkr_etl.position_recovery import insert_missing_positions
from ibkr_etl.util import canonical_json, decimal_text, pick
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
from azure.core.exceptions import ResourceExistsError

TENANT='00000000-0000-0000-0000-000000000001'
MODE=os.environ['ALPHA_MS_RECOVERY_MODE']
if MODE not in ('preview','apply'):raise ValueError('Explicit recovery mode required')
TARGET=os.environ['ALPHA_MS_RECOVERY_TARGET']
AUDIT_LABEL=os.environ.get('ALPHA_MS_RECOVERY_AUDIT_LABEL',TARGET+'-'+MODE)
if AUDIT_LABEL not in (TARGET+'-'+MODE,TARGET+'-idempotence'):
    raise ValueError('Recovery audit label outside registered scope')
HOSTS={'legacy':'ibkrv11-pg-5rcc62xxqblay.postgres.database.azure.com',
       'current':'ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com'}
if TARGET not in HOSTS:raise ValueError('Database target outside authorized recovery scope')


def sha(raw):return hashlib.sha256(raw).hexdigest()


def protected_fingerprint(c):
    """Existing broker facts and raw archive registrations, not derived outputs."""
    names=['accounts','instruments','nav_snapshots','cash_snapshots','trades','cash_transactions','fx_rates','corporate_actions','fx_transactions']
    result={}
    for table in names:
        # Fixed trusted identifiers, streamed rather than logging sensitive rows.
        h=hashlib.sha256();count=0
        for row in c.execute('SELECT to_jsonb(t)::text AS value FROM '+table+' t ORDER BY to_jsonb(t)::text'):
            h.update(row[0].encode());h.update(b'\n');count+=1
        result[table]={'rows':count,'sha256':h.hexdigest()}
    return result


def main():
    credential=DefaultAzureCredential(exclude_interactive_browser_credential=True)
    source=BlobServiceClient('https://ibkrv115rcc62xxqblay.blob.core.windows.net',credential=credential)
    parsed=[]
    for s in SOURCES:
        if TARGET == 'current':
            content=zlib.decompress(base64.b64decode(SOURCE_BYTES[s['sha256']]))
            destination=BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],credential=credential)
            name=TENANT+'/recovery/20261007/flex_'+s['date']+'_'+s['sha256']+'.xml'
            blob=destination.get_blob_client('ibkr-raw',name)
            try:
                blob.upload_blob(content,overwrite=False,content_settings=ContentSettings(content_type='application/xml'),
                    metadata={'sha256':s['sha256'],'source_type':'original_ibkr_flex_xml'})
            except ResourceExistsError:
                if blob.download_blob().readall()!=content:raise ValueError('Existing source archive differs')
            if sha(blob.download_blob().readall())!=s['sha256']:raise ValueError('Copied original source differs')
            s=dict(s,target_raw_path=os.environ['AZURE_STORAGE_ACCOUNT_URL']+'/ibkr-raw/'+name)
        else:
            content=source.get_blob_client('raw-statements',s['blob']).download_blob().readall()
        if sha(content)!=s['sha256'] or len(content)!=s['bytes']:raise ValueError('Original XML archive readback differs')
        report=parse_flex_xml(content)
        positions=[r for r in report.rows if r.record_type.lower()=='openposition' and r.account_id in ('U16088571','U16088606')]
        if any(r.report_date!=s['date'] for r in positions):raise ValueError('Incorrect returned source date')
        parsed.append((s,report,positions))
    with psycopg.connect(os.environ['DATABASE_URL'],host=HOSTS[TARGET],row_factory=_hybrid_row_factory,
            connect_timeout=25,options='-c statement_timeout=180000 -c lock_timeout=15000') as raw:
        c=PostgresConnection(raw)
        c.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE')
        c.execute("SELECT set_config('app.tenant_id',?,true)",(TENANT,))
        # Reject overlap with the scheduled daily pipeline as well as recovery.
        if not c.execute("SELECT pg_try_advisory_xact_lock(hashtext(?))",('ibkr-daily:'+TENANT,)).fetchone()[0]:
            raise ValueError('Scheduled ingestion is active; no recovery writes allowed')
        c.execute('LOCK TABLE position_snapshots,flex_rows IN SHARE ROW EXCLUSIVE MODE')
        before=protected_fingerprint(c)
        for control in CONTROLS['nav_controls']:
            nav=c.execute('SELECT total_nav,stock FROM v_daily_nav WHERE account_id=? AND snapshot_date=? AND currency=?',
                (control['account_id'],control['date'],'USD')).fetchone()
            if not nav or D(str(nav[0]))!=D(control['total']) or abs(D(str(nav[1]))-D(control['stock']))>D('.00001'):
                raise ValueError('Existing physical NAV disagrees with broker source; no overwrite allowed')
        counts=[]
        for s,report,positions in parsed:
            for a in ('U16088571','U16088606'):
                p=[r for r in positions if r.account_id==a]
                stock=sum(D(pick(r.attributes,'positionValue','marketValue'))*D(pick(r.attributes,'fxRateToBase',default='1')) for r in p)
                expected=next(x for x in CONTROLS['nav_controls'] if x['account_id']==a and x['date']==s['date'])
                if abs(stock-D(expected['stock']))>D('.00001'):raise ValueError('Flex holdings do not reconcile to existing stock NAV')
                for r in p:
                    currency=r.currency
                    if currency=='USD':continue
                    q=c.execute('SELECT rate_text FROM fx_rates WHERE account_id=? AND rate_date=? AND from_currency=? AND to_currency=?',
                        (a,s['date'],currency,'USD')).fetchone()
                    if not q or D(q[0])!=D(pick(r.attributes,'fxRateToBase')):
                        raise ValueError('Required exact-date FX observation is absent or conflicts; no source change allowed')
            now=datetime.now(timezone.utc).isoformat()
            location=s.get('target_raw_path','https://ibkrv115rcc62xxqblay.blob.core.windows.net/raw-statements/'+s['blob'])
            run=c.execute('''SELECT run_id FROM ingestion_runs WHERE tenant_id=? AND run_type='missing-position-recovery'
                AND raw_sha256=? AND status='success' ''',(TENANT,s['sha256'])).fetchone()
            controls=[x for x in CONTROLS['positions'] if x['date']==s['date']]
            if run:
                run_id=run[0]
            else:
                run_id=c.execute('''INSERT INTO ingestion_runs(run_type,requested_from,requested_to,started_at,completed_at,
                    status,raw_sha256,raw_path,row_count,reconciliation_json,tenant_id)
                    VALUES('missing-position-recovery',?,?,?,?, 'success',?,?,?,?,?) RETURNING run_id''',
                    (s['date'],s['date'],now,now,s['sha256'],location,len(positions),canonical_json({'mode':MODE,'csv_sources':CONTROLS['sources'],'source_tables_overwritten':False}),TENANT)).fetchone()[0]
            c.execute('''INSERT INTO raw_reports(run_id,received_at,content_sha256,content_type,byte_count,file_path,tenant_id)
                VALUES(?,?,?,'application/xml',?,?,?) ON CONFLICT(content_sha256) DO NOTHING''',
                (run_id,now,s['sha256'],s['bytes'],location,TENANT))
            inserted=insert_missing_positions(c,positions,controls,run_id)
            for a,wanted in (('U16088571',43),('U16088606',33)):
                count=c.execute('SELECT count(*) FROM position_snapshots WHERE account_id=? AND snapshot_date=?',(a,s['date'])).fetchone()[0]
                if count!=wanted:raise ValueError('Post-import completeness failed')
            counts.append({'date':s['date'],'positions_inserted':inserted,'run_id':run_id,'sha256':s['sha256']})
        after=protected_fingerprint(c)
        if before!=after:raise ValueError('Existing broker facts changed; entire transaction rejected')
        if MODE=='preview':c.rollback()
        else:c.commit()
    result={'status':'rollback_validated' if MODE=='preview' else 'missing_positions_committed',
        'target':TARGET,'mode':MODE,'counts':counts,'existing_broker_facts_unchanged':before==after,
        'source_xml_unchanged':True,'published_snapshots_changed':False,'reports_released':False,
        'protection_fingerprints':before,'csv_sources':CONTROLS['sources']}
    service=BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],credential=credential)
    container=os.environ.get('INTERNAL_REPORT_CONTAINER',os.environ.get('AZURE_REPORT_CONTAINER','internal-reports'))
    blob=service.get_blob_client(container,'review/alpha-world-ms/source-recovery-20261007/'+AUDIT_LABEL+'.json')
    data=json.dumps(result,sort_keys=True,indent=2).encode()
    blob.upload_blob(data,overwrite=False,content_settings=ContentSettings(content_type='application/json'))
    if sha(blob.download_blob().readall())!=sha(data):raise ValueError('Audit evidence readback failed')
    print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({k:v for k,v in result.items() if k!='protection_fingerprints'}),flush=True)


if __name__=='__main__':
    try:main()
    except Exception as e:
        print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'stopped','target':TARGET,'mode':MODE,
            'error_class':type(e).__name__,'error':str(e) if isinstance(e,ValueError) else 'Details retained privately; credentials redacted'}),flush=True)
        raise SystemExit(1)
