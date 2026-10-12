"""Bound, MS-only exact-writer qualification/apply/verification; no report entrypoints."""
import hashlib
import json
import os

import psycopg
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
import ms_website_cutover_writer as writer
from ibkr_etl import web
from ibkr_etl.db import _hybrid_row_factory

TABLES=('portfolio_benchmark_mapping','performance_comparison_daily','audit_events')


def sha(content):return hashlib.sha256(content).hexdigest()


def main():
    service=BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],credential=DefaultAzureCredential(
        exclude_interactive_browser_credential=True))
    def download(name,digest):
        blob=service.get_blob_client('internal-reports',name)
        data=blob.download_blob().readall()
        writer.require(sha(data)==digest and blob.get_blob_properties().metadata.get('sha256')==digest,
            'Bound source blob integrity differs')
        return json.loads(data)
    inputs=download(BINDING['input_blob'],BINDING['inputs_sha256'])
    prior_proof=download(BINDING['native_proof_blob'],BINDING['native_proof_sha256'])
    results=writer.comparisons(inputs,prior_proof)
    mode=BINDING['mode'];writer.require(mode in ('qualify','apply','verify','rollback'),'Unknown cutover mode')
    output={'mode':mode,'binding':BINDING,'factsheets_changed':False,'ibkr_source_changed':False,
        'reports_released':False,'approvals_written':False,'onboarding_activated':False}
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=_hybrid_row_factory,connect_timeout=25,
            options='-c default_transaction_read_only=on -c statement_timeout=120000 -c lock_timeout=10000') as c:
        c.execute("SELECT set_config('app.tenant_id',%s,false)",(writer.TENANT,))
        role=c.execute('SELECT current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone()
        writer.require(not role['rolsuper'] and not role['rolbypassrls'],'Restricted non-administrator role required')
        for name,privilege in (('portfolio_benchmark_mapping','INSERT,UPDATE,DELETE'),
                ('performance_comparison_daily','INSERT'),('audit_events','INSERT')):
            for operation in privilege.split(','):
                row=c.execute('SELECT has_table_privilege(current_user,%s,%s) AS allowed',('public.'+name,operation)).fetchone()
                writer.require(row['allowed'],'Restricted service lacks required cutover privilege: '+name+':'+operation)
        seq=c.execute("SELECT pg_get_serial_sequence('public.audit_events','audit_event_id') AS name").fetchone()['name']
        writer.require(seq and c.execute('SELECT has_sequence_privilege(current_user,%s,\'USAGE\') AS allowed',(seq,)).fetchone()['allowed'],
            'Restricted service lacks audit sequence privilege')
        def source_checks(exclude=()):
            for name,query in BINDING['source_queries'].items():
                if name in exclude:continue
                observed=[dict(r) for r in c.execute(query,(writer.TENANT,))]
                writer.require(writer.canonical(observed)==writer.canonical(inputs[name]),'Current source/configuration drift: '+name)
        def public_fingerprint():
            return {name:sha(writer.canonical([dict(r) for r in c.execute(query,(writer.TENANT,))]).encode())
                for name,query in BINDING['fingerprint_queries'].items()}
        source_checks(exclude=('mappings','comparison_inventory') if mode in ('verify','rollback') else ())
        before=public_fingerprint();c.commit()
        if mode=='qualify':
            c.execute('SET TRANSACTION READ WRITE')
            for name in TABLES:c.execute('CREATE TEMP TABLE '+name+' (LIKE public.'+name+' INCLUDING ALL)')
            c.execute('CREATE TEMP SEQUENCE ms_exact_cutover_audit_id')
            c.execute("ALTER TABLE pg_temp.audit_events ALTER COLUMN audit_event_id SET DEFAULT nextval('pg_temp.ms_exact_cutover_audit_id')")
            for name in TABLES[:2]:
                c.execute('INSERT INTO pg_temp.'+name+' SELECT * FROM public.'+name+' WHERE tenant_id=%s AND subject_id=%s',
                    (writer.TENANT,writer.SUBJECT))
            c.commit();c.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE READ ONLY')
            def fingerprint_temp():
                return {name:sha(writer.canonical([dict(r) for r in c.execute('SELECT * FROM pg_temp.'+name+
                    (' ORDER BY benchmark_id,effective_from' if name==TABLES[0] else
                     ' ORDER BY benchmark_id,observation_date,calculation_version' if name==TABLES[1] else ' ORDER BY audit_event_id'))]).encode())
                    for name in TABLES}
            frozen=fingerprint_temp()
            try:writer.apply(c,namespace='pg_temp',inputs=inputs,results=results,day=BINDING['release_day'],
                stamp=BINDING['release_stamp'],binding=BINDING['release_reference'],failure_after_rows=100)
            except RuntimeError as error:
                writer.require(str(error)=='Injected exact writer failure','Unexpected injected failure')
            else:raise ValueError('Expected exact writer failure did not occur')
            c.commit();writer.require(fingerprint_temp()==frozen,'Caller commit retained partial exact cutover')
            c.commit();c.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE READ ONLY')
            n=writer.apply(c,namespace='pg_temp',inputs=inputs,results=results,day=BINDING['release_day'],
                stamp=BINDING['release_stamp'],binding=BINDING['release_reference'])
            payload=web._onboarding_preview_payload(c,writer.SUBJECT)
            writer.require(payload['validation']['ready_for_owner_preview_approval'],'Exact-writer preview failed')
            for result in results:
                rows=[dict(r) for r in c.execute('SELECT '+','.join(writer.ROW_COLUMNS)+' FROM pg_temp.performance_comparison_daily '
                    'WHERE tenant_id=%s AND subject_id=%s AND benchmark_id=%s AND calculation_version=%s ORDER BY observation_date',
                    (writer.TENANT,writer.SUBJECT,result.benchmark_id,writer.MS_HKD_COMPARISON_VERSION))]
                writer.require(writer.canonical(rows)==writer.canonical([dict((key,getattr(row,key)) for key in writer.ROW_COLUMNS)
                    for row in result.rows]),'Exact written comparison differs from qualified calculation')
            applied=fingerprint_temp();c.commit();c.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE READ ONLY')
            writer.rollback(c,namespace='pg_temp',inputs=inputs,day=BINDING['release_day'],
                stamp=BINDING['release_stamp'],binding=BINDING['release_reference'])
            c.commit();restored=fingerprint_temp()
            writer.require(restored[TABLES[0]]==frozen[TABLES[0]] and restored[TABLES[1]]==applied[TABLES[1]],
                'Exact rollback lost history or failed original selection recovery')
            after=public_fingerprint();writer.require(before==after,'Isolated writer changed persistent data')
            output.update(status='exact_writer_and_rollback_qualified',comparison_rows=n,
                failure_after_100_rows_rolled_back=True,caller_commit_safe=True,selection_rollback_passed=True,
                comparison_history_retained=True,persistent_database_writes=0,public_before=before,public_after=after,
                preview_validation=payload['validation'])
        else:
            c.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE '+('READ ONLY' if mode=='verify' else 'READ WRITE'))
            if mode=='apply':
                source_checks()
                n=writer.apply(c,namespace='public',inputs=inputs,results=results,day=BINDING['release_day'],
                    stamp=BINDING['release_stamp'],binding=BINDING['release_reference'])
            elif mode=='rollback':
                source_checks(exclude=('mappings','comparison_inventory'))
                writer.rollback(c,namespace='public',inputs=inputs,day=BINDING['release_day'],
                    stamp=BINDING['release_stamp'],binding=BINDING['release_reference']);n=0
            else:n=sum(len(r.rows) for r in results)
            if mode!='rollback':
                writer.require(writer.canonical(writer.mappings(c,'public'))==writer.canonical(writer.current_candidate_mapping(
                    inputs,BINDING['release_day'],BINDING['release_stamp'])),'Live mapping selection differs')
                for result in results:
                    rows=[dict(r) for r in c.execute('SELECT '+','.join(writer.ROW_COLUMNS)+' FROM public.performance_comparison_daily '
                        'WHERE tenant_id=%s AND subject_id=%s AND benchmark_id=%s AND calculation_version=%s ORDER BY observation_date',
                        (writer.TENANT,writer.SUBJECT,result.benchmark_id,writer.MS_HKD_COMPARISON_VERSION))]
                    writer.require(writer.canonical(rows)==writer.canonical([dict((key,getattr(row,key)) for key in writer.ROW_COLUMNS)
                        for row in result.rows]),'Live comparison rows differ')
                payload=web._onboarding_preview_payload(c,writer.SUBJECT)
                writer.require(payload['validation']['ready_for_owner_preview_approval'],'Live preview not ready')
                output['preview_validation']=payload['validation']
            source_checks(exclude=('mappings','comparison_inventory'))
            after=public_fingerprint()
            writer.require(all(before[k]==after[k] for k in before if k not in ('mappings','comparisons','audit')),
                'Persistent write outside authorized cutover scope')
            c.commit()
            output.update(status='live_'+mode+'_verified',comparison_rows=n,persistent_database_writes=mode!='verify',
                public_before=before,public_after=after)
        c.rollback()
    content=json.dumps(output,sort_keys=True,separators=(',',':'),default=str,allow_nan=False).encode()
    blob=service.get_blob_client('internal-reports',BINDING['output_blob'])
    blob.upload_blob(content,overwrite=False,metadata={'sha256':sha(content)},content_settings=ContentSettings(content_type='application/json'))
    writer.require(blob.download_blob().readall()==content,'Private cutover proof readback differs')
    print('MS_EXACT_CUTOVER '+json.dumps({'status':output['status'],'comparison_rows':output['comparison_rows'],
        'persistent_database_writes':output['persistent_database_writes'],'proof_sha256':sha(content)}),flush=True)


if __name__=='__main__':main()
