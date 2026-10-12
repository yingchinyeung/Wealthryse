"""Fresh isolated v2 PITR and exact-image application recovery; never redirect production."""
import argparse,base64,copy,gzip,hashlib,inspect,json,re
from datetime import datetime,timedelta,timezone
from pathlib import Path
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
import run_ms_website_input_probe as p
from read_ms_website_input_probe import verify_env
from preflight_viewer_maintenance_20261008 import FOLDER,SERVERS,API

NAME='ibkrv2stg-app-recovery-20261008'
PATH=p.release.BASE+'Microsoft.DBforPostgreSQL/flexibleServers/'+NAME
SOURCE=p.release.BASE+'Microsoft.DBforPostgreSQL/flexibleServers/'+SERVERS[0]
ROOT=FOLDER/'application-recovery'

def restored_database_url(value):
    """Keep the URI contract, credentials and options; change only the clone host."""
    from urllib.parse import urlsplit, urlunsplit
    parsed=urlsplit(value)
    if parsed.scheme not in ('postgres','postgresql') or parsed.hostname!='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com':
        raise ValueError('Unexpected credential source URI')
    userinfo,separator,host=parsed.netloc.rpartition('@')
    if not separator or not userinfo or not host:
        raise ValueError('Expected existing credential URI authority')
    authority=userinfo+'@ibkrv2stg-app-recovery-20261008.postgres.database.azure.com'
    if parsed.port is not None:authority+=':'+str(parsed.port)
    return urlunsplit((parsed.scheme,authority,parsed.path,parsed.query,parsed.fragment))

def save(folder,name,record):
    p.release.build.save(folder,name,{**record,'protected_receipt':p.retain(record,'viewer-app-recovery-'+name)})

def read(folder,name):
    value=p.release.read(folder,name);p.verify_protected(value);return value

def scope(server):
    return {k:server[k] for k in ('id','sku','location')}|{k:server['properties'][k] for k in ('network','highAvailability','storage','version','backup')}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=('prepare','restore','state','probe-prepare','probe-dispatch','probe-inspect'));parser.add_argument('--mode',choices=('source','restored','post_resize','post_ha'),default='source');parser.add_argument('--attempt',choices=('v1','v2','v3'),default='v2');args=parser.parse_args()
    source=p.request('GET',SOURCE+API)
    if args.stage=='prepare':
        if ROOT.exists() or p.request('GET',PATH+API,missing=True) is not None:raise ValueError('Preserve prior recovery attempt')
        preflight=read(FOLDER,'preflight')
        if scope(source)!=scope(preflight['databases'][0]['baseline']):raise ValueError('Source database drift')
        if source['properties']['state']!='Ready':raise ValueError('Source not ready')
        previous=p.release.read(p.ROOT/'.work/system-improvements-20261006/ha-rehearsal','failover-qualified')
        recovered,_=p.restore_protected(previous['protected_receipt'])
        if recovered!=previous['record']:raise ValueError('Protected earlier failover evidence differs')
        if previous['record']['ha']['state']!='Healthy':raise ValueError('Isolated failover evidence required')
        point=(datetime.now(timezone.utc)-timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M:%SZ')
        ROOT.mkdir()
        record={'source':SOURCE,'target':PATH,'restore_point':point,'source_baseline':scope(source),'website_image':preflight['website_image'],'authorization':'Owner requested repair resource-group issues and selected immediate maintenance; fresh isolated recovery before production HA; no production connection changes','production_mutations':0,'disposition':'Retain until scoped recovery evidence and maintenance review; no automatic deletion'}
        save(ROOT,'plan',record);print(json.dumps({'prepared':True,'isolated_target':NAME,'restore_point':point}));return
    plan=read(ROOT,'plan')
    post=args.mode in ('post_resize','post_ha')
    if post:
        from deploy_viewer_database_ha_20261008 import ROOT as PRODUCTION,invariant,SKU
        maintenance=read(PRODUCTION,'plan')
        read(PRODUCTION,'resize-intent')
        if invariant(source)!=invariant(maintenance['baseline']) or source['sku']!=SKU or source['properties']['state']!='Ready':raise ValueError('Post-maintenance server scope differs')
        ha=source['properties']['highAvailability']
        if args.mode=='post_resize' and ha['mode']!='Disabled':raise ValueError('Post-resize requires HA still disabled')
        if args.mode=='post_ha':
            read(PRODUCTION,'enable-ha-intent')
            if ha['mode']!='ZoneRedundant' or ha['state']!='Healthy' or ha.get('standbyAvailabilityZone')==source['properties'].get('availabilityZone'):raise ValueError('Healthy zone-redundant protection required')
    elif scope(source)!=plan['source_baseline']:raise ValueError('Production database drift before isolated qualification')
    target=p.request('GET',PATH+API,missing=True)
    if target and (target['name']!=NAME or target['properties']['network']!=source['properties']['network']):raise ValueError('Recovery target network/identity drift')
    if args.stage=='restore':
        source_proof=read(ROOT/('source-'+args.attempt),'qualified')
        if not source_proof['registered_report_bytes_verified'] or target is not None or (ROOT/'restore-intent.json').exists():raise ValueError('Source proof or one-shot restore gate failed')
        body={'location':source['location'],'tags':{'application':'ibkr-portfolio-platform','purpose':'isolated-application-recovery','owner':'portfolio-platform','reviewAfter':'2026-10-09'},'properties':{'createMode':'PointInTimeRestore','sourceServerResourceId':SOURCE,'pointInTimeUTC':plan['restore_point'],'availabilityZone':'1','network':source['properties']['network']}}
        save(ROOT,'restore-intent',{'body':body,'dispatched_at':datetime.now(timezone.utc).isoformat()})
        response=p.request('PUT',PATH+API,data=body)
        save(ROOT,'restore-ack',{'id':response.get('id'),'state':response.get('properties',{}).get('state'),'production_mutations':0})
        print('Dispatched isolated application recovery; production unchanged');return
    if args.stage=='state':
        print(json.dumps({'target':NAME,'exists':target is not None,'state':target['properties'].get('state') if target else None,'production_mutations':0}));return
    if args.mode=='restored' and (target is None or target['properties']['state']!='Ready'):raise ValueError('Isolated restored server must be Ready')
    folder=ROOT/(args.mode+'-'+args.attempt);job=p.request('GET',p.release.path(p.JOB)+p.release.API)
    if args.stage=='probe-prepare':
        if folder.exists():raise ValueError('Preserve prior probe')
        original=read(p.ROOT/'.work/viewer-report-investigation-20261008/native-candidate-v2','intent')
        if original['image']!=plan['website_image']:raise ValueError('Exact installed website image required')
        chunks=original['template']['containers'][0]['args'][2:]
        code=gzip.decompress(base64.b64decode(''.join(chunks))).decode().replace('native-candidate-v2.json','recovery-'+args.mode+'-viewer-'+args.attempt+'.json')
        prefix="import os\nfrom psycopg.conninfo import conninfo_to_dict,make_conninfo\ninfo=conninfo_to_dict(os.environ['DATABASE_URL'])\nif info.get('host')!='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com':raise ValueError('Unexpected credential source host')\n"
        if args.mode=='restored':prefix+=inspect.getsource(restored_database_url)+"\nos.environ['DATABASE_URL']=restored_database_url(os.environ['DATABASE_URL'])\n"
        extra=(p.ROOT/'scripts/probe_viewer_application_recovery_20261008.py').read_text(encoding='utf-8')
        code=prefix+code+'\nRECOVERY_MODE='+repr(args.mode)+'\nRECOVERY_ATTEMPT='+repr(args.attempt)+'\n'+extra
        template=p.corrected_template(job,code);c=template['containers'][0];c['image']=plan['website_image']
        encoded=base64.b64encode(gzip.compress(code.encode(),mtime=0)).decode()
        c['args']=['-c',"import sys,base64,gzip;exec(compile(gzip.decompress(base64.b64decode(''.join(sys.argv[1:]))),'private-application-recovery','exec'))"]+[encoded[i:i+32000] for i in range(0,len(encoded),32000)]
        c['env']=[e for e in c['env'] if e['name']!='AUTHORIZATION_MODE']+[{'name':'AUTHORIZATION_MODE','value':'database'}]
        p.release.idle(p.JOB);folder.mkdir();save(folder,'intent',{'mode':args.mode,'template':template,'source_sha256':hashlib.sha256(code.encode()).hexdigest(),'settings_sha256':p.release.settings_invariant(job),'database_writes':0})
        print('Prepared exact-image '+args.mode+' application recovery probe');return
    intent=read(folder,'intent')
    if p.release.settings_invariant(job)!=intent['settings_sha256']:raise ValueError('Manual reporting worker settings drift')
    if args.stage=='probe-dispatch':
        if (folder/'dispatch-intent.json').exists():raise ValueError('Inspect rather than redispatch')
        p.release.idle(p.JOB);p.no_matching_execution(p.JOB,[intent['template']['containers'][0]['args']])
        save(folder,'dispatch-intent',{'intent_sha256':p.sha(folder/'intent.json')})
        response=p.request('POST',p.release.path(p.JOB)+'/start'+p.release.API,data=intent['template'])
        if not response.get('name'):raise ValueError('Unknown dispatch; inspect')
        save(folder,'dispatch',{'execution':response['name']});print(json.dumps({'execution':response['name'],'mode':args.mode}));return
    execution=read(folder,'dispatch')['execution']
    if not re.fullmatch(re.escape(p.JOB)+'-[a-z0-9]{7}',execution):raise ValueError('Execution identity differs')
    run=p.request('GET',p.release.path(p.JOB)+'/executions/'+execution+p.release.API)
    print(json.dumps({'status':run['properties']['status'],'execution':execution,'mode':args.mode}),flush=True)
    if run['properties']['status']!='Succeeded':return
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args','name')):raise ValueError('Exact recovery runtime differs')
    verify_env(wanted['env'],actual['env'],job)
    service=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=AzureCliCredential())
    outputs={}
    for kind in ('viewer','reports'):
        blob=service.get_blob_client('internal-reports','review/viewer-report-investigation-20261008/recovery-'+args.mode+'-'+kind+'-'+args.attempt+'.json')
        data=blob.download_blob().readall()
        if blob.get_blob_properties().metadata.get('sha256')!=hashlib.sha256(data).hexdigest():raise ValueError('Recovery proof hash differs')
        value=json.loads(data);outputs[kind]=value;file=folder/(kind+'-proof.json')
        if not file.exists():file.write_bytes(data)
        elif file.read_bytes()!=data:raise ValueError('Preserve recovery proof')
    checks=outputs['viewer']['checks'];reports=outputs['reports']
    errors=[r for r in checks if r.get('error_type') or r.get('passed') is False or r.get('status',200)!=200 and not (r.get('status')==409 and r['endpoint']=='export/trades.csv' and r['subject_id'] in ('alpha-world-ms','hong-kong-china'))]
    if len(checks)!=208 or errors or reports['status']!='passed' or reports['database_writes']!=0 or not reports['anonymous_reports_denied'] or not reports['viewer_review_access_denied']:raise ValueError('Recovery runtime failed')
    parity=None
    if args.mode!='source':
        previous=json.loads((ROOT/'source-v2'/'reports-proof.json').read_bytes())
        parity=all(previous[key]==reports[key] for key in ('schema_sha256','tables','downloads','owner_review_downloads'))
        if not parity:raise ValueError('Source/restore report, authorization or table fingerprints differ; reconcile restore window')
    record={'status':'passed','mode':args.mode,'execution':execution,'image':plan['website_image'],'viewer_checks':208,'tables':len(reports['tables']),'registered_report_bytes_verified':True,'approved_downloads':len(reports['downloads']),'ms_owner_review_downloads':len(reports['owner_review_downloads']),'source_parity':parity,'database_writes':0,'production_mutations':0,'gateway_sign_in_tested':False,'viewer_proof_sha256':p.sha(folder/'viewer-proof.json'),'report_proof_sha256':p.sha(folder/'reports-proof.json')}
    if not (folder/'qualified.json').exists():save(folder,'qualified',record)
    if post:
        name='post-resize-application-qualified' if args.mode=='post_resize' else 'ha-qualified'
        if not (PRODUCTION/(name+'.json')).exists():save(PRODUCTION,name,{**record,'server_sku':source['sku'],'server_ha':source['properties']['highAvailability'],'source_data_access_reports_and_approvals_unchanged':True})
    print(json.dumps(record))

if __name__=='__main__':main()
