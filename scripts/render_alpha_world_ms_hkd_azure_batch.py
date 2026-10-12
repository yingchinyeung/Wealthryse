"""Read-only Azure HKD/HIBOR batch qualification; no production cutover."""
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import psycopg
from psycopg.rows import dict_row
from azure.core.exceptions import ResourceExistsError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
from ibkr_etl.monthly_artifacts import render_factsheet, factsheet_layout_version
from ibkr_etl.alpha_world_ms_hkd import rebuild, factsheet_snapshot

ROOT=Path(__file__).resolve().parents[1]
SUBJECT='alpha-world-ms'
TENANT='00000000-0000-0000-0000-000000000001'
CONTAINER='internal-reports'
PREFIX='review/alpha-world-ms/hkd-hibor-v14r8-20261007/'


def sha(data):return hashlib.sha256(data).hexdigest()


def main():
    release_bytes=(ROOT/'ALPHA_MS_HKD_RELEASE.json').read_bytes()
    release=json.loads(release_bytes)
    if release['layout_version']!=factsheet_layout_version(SUBJECT):raise ValueError('Wrong installed layout')
    for name,digest in release['installed_files_sha256'].items():
        if sha((ROOT/name).read_bytes())!=digest:raise ValueError('Installed source differs')
    image=os.environ['ALPHA_MS_IMAGE_DIGEST']
    if not image.startswith('sha256:') or len(image)!=71:raise ValueError('Immutable image digest required')
    storage=BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],
        credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
    def get(name,digest):
        data=storage.get_blob_client(CONTAINER,PREFIX+'inputs/'+name).download_blob().readall()
        if sha(data)!=digest:raise ValueError('Private input integrity differs: '+name)
        return data
    source=json.loads(get('source-inspection.json',release['inputs_sha256']['source-inspection.json']))
    fixings=json.loads(get('official-hibor.json',release['inputs_sha256']['official-hibor.json']))
    capture=json.loads(get('capture.json',release['inputs_sha256']['capture.json']))
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row,
            options='-c default_transaction_read_only=on -c statement_timeout=120000',connect_timeout=25) as connection:
        connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        connection.execute("SELECT set_config('app.tenant_id',%s,true)",(TENANT,))
        assets=connection.execute('''SELECT asset_role,container_name,blob_name,sha256,approval_status
            FROM subject_report_template_assets WHERE tenant_id=%s AND subject_id=%s
            AND report_type='factsheet' AND is_current''',(TENANT,SUBJECT)).fetchall()
        parents=connection.execute('''SELECT snapshot_id::text,data_sha256 FROM reporting_snapshots
            WHERE tenant_id=%s AND subject_id=%s AND status IN ('frozen','approved')''',(TENANT,SUBJECT)).fetchall()
        connection.rollback()
    by_role={a['asset_role']:a for a in assets};by_id={r['snapshot_id']:r['data_sha256'] for r in parents}
    if len(assets)!=2 or set(by_role)!={'template','completed_example'}:raise ValueError('Approved design pair required')
    for item in capture['assets']:
        if any(by_role[item['asset_role']][k]!=item[k] for k in ('container_name','blob_name','sha256','approval_status')):
            raise ValueError('Design authority changed')
    for item in capture['snapshots']:
        if by_id.get(item['snapshot_id'])!=item['data_sha256']:raise ValueError('Frozen parent changed')
    if len(capture['expected_periods'])!=21:raise ValueError('Unexpected historical scope')
    history=rebuild(source)
    if history['contract_sha256']!=release['contract_sha256']:raise ValueError('HKD calculation definition differs')
    def put(name,data,content_type):
        blob=storage.get_blob_client(CONTAINER,PREFIX+'outputs/'+name)
        try:blob.upload_blob(data,overwrite=False,content_settings=ContentSettings(content_type=content_type),
            metadata={'sha256':sha(data),'subject_id':SUBJECT,'layout':'v14r8'})
        except ResourceExistsError:
            if blob.download_blob().readall()!=data:raise ValueError('Existing private artifact differs')
        if blob.download_blob().readall()!=data:raise ValueError('Private output readback differs')
    artifacts=[]
    with TemporaryDirectory(prefix='alpha-ms-hkd-') as directory:
        folder=Path(directory)
        for role,item in by_role.items():
            data=storage.get_blob_client(item['container_name'],item['blob_name']).download_blob().readall()
            if sha(data)!=item['sha256']:raise ValueError('Design bytes differ')
            (folder/(role+'.pdf')).write_bytes(data)
        for period in capture['expected_periods']:
            snapshot=factsheet_snapshot(history,fixings,period)
            content=json.dumps(snapshot,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
            expected=release['snapshot_sha256'][period]
            if sha(content)!=expected:raise ValueError('Azure HKD calculation differs from independent local qualification')
            name='AlphaWorld_MS_Factsheet_'+period+'.pdf';target=folder/name
            result=render_factsheet(snapshot,template_path=folder/'template.pdf',
                design_model_path=folder/'completed_example.pdf',output_path=target)
            if json.dumps(snapshot,sort_keys=True,separators=(',',':'),allow_nan=False).encode()!=content:
                raise ValueError('Renderer mutated inputs')
            pdf=target.read_bytes();put(name,pdf,'application/pdf')
            artifacts.append({'period_end':period,'data_sha256':sha(content),'sha256':sha(pdf),
                'blob_name':PREFIX+'outputs/'+name,'render':result})
            print(json.dumps({'period':period,'status':'private_hkd_rendered_in_azure'}),flush=True)
    record={'status':'private_hkd_rendered_in_azure','count':len(artifacts),'image_digest':image,
        'release_manifest_sha256':sha(release_bytes),'artifacts':artifacts,'database_writes':0,
        'broker_data_changed':False,'published_history_changed':False,'production_cutover':False,
        'reports_released':False,'onboarding_activated':False,'commercial_usage_rights_owner_confirmed':True}
    put('manifest.json',json.dumps(record,sort_keys=True,indent=2).encode(),'application/json')
    print(json.dumps({k:v for k,v in record.items() if k!='artifacts'}),flush=True)


if __name__=='__main__':main()
