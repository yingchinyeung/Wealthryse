"""Source-only qualification for the existing restricted metadata monitor.

No image, identity, grant, schedule or financial producer can be changed here.
Native execution and independently collected metadata remain required.
"""
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re

SUB = '3956e821-cee4-403f-8708-e84e2ed490eb'
BASE = '/subscriptions/'+SUB+'/resourceGroups/rg-ibkr-database-v11/providers/'
JOB = 'ibkrv2stg-metadata-monitor'
IDENTITY = BASE+'Microsoft.ManagedIdentity/userAssignedIdentities/'+JOB+'-identity'
IMAGE = 'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:276a4783bf4ddf437bd5d449e08df39729fb43f449975082af80fd4c13e1142c'
ENVIRONMENT = BASE+'Microsoft.App/managedEnvironments/ibkrv2stg-env'


def decode_bundle(args):
    if not isinstance(args,list) or len(args)!=2 or args[0]!='-c' or not isinstance(args[1],str):
        raise ValueError('Exact reviewed monitor bundle wrapper required')
    match=re.fullmatch(r"import base64;exec\(compile\(base64.b64decode\('([A-Za-z0-9+/=]+)'\),'metadata-monitor','exec'\)\)",args[1])
    if not match or len(args[1])>350000:
        raise ValueError('Invalid or oversized monitor source bundle')
    try:
        value=base64.b64decode(match[1],validate=True)
        text=value.decode('utf-8')
        compile(text,'metadata-monitor','exec')
    except (ValueError,UnicodeError,SyntaxError):
        raise ValueError('Invalid frozen monitor source') from None
    if not 1<=len(value)<=256000:
        raise ValueError('Bounded monitor source required')
    return value


def writable_definition(job):
    """Retain writable configuration, never resolved secret values or ARM status."""
    identity=job['identity']
    config=job['properties']['configuration']
    if any(s.get('value') for s in config.get('secrets',[])):
        raise ValueError('Resolved secret values must not be retained or replayed')
    result={k:deepcopy(job[k]) for k in ('location','tags')}
    result['identity']={'type':identity['type'],
        'userAssignedIdentities':{name:{} for name in identity['userAssignedIdentities']}}
    result['properties']={key:deepcopy(job['properties'][key])
        for key in ('environmentId','configuration','template')}
    if 'workloadProfileName' in job['properties']:
        result['properties']['workloadProfileName']=job['properties']['workloadProfileName']
    return result


def validate_baseline(job):
    if job.get('id','').lower()!=(BASE+'Microsoft.App/jobs/'+JOB).lower() or job.get('location') not in {'eastasia','East Asia'}:
        raise ValueError('Exact existing metadata monitor required')
    identity=job.get('identity',{})
    if identity.get('type')!='UserAssigned' or {k.lower() for k in identity.get('userAssignedIdentities',{})}!={IDENTITY.lower()}:
        raise ValueError('Exact restricted identity required')
    props=job['properties']; config=props['configuration']
    if props.get('environmentId','').lower()!=ENVIRONMENT.lower():
        raise ValueError('Existing private application environment required')
    if (config.get('triggerType')!='Schedule' or config.get('replicaTimeout')!=180
        or type(config.get('replicaRetryLimit')) is not int or config['replicaRetryLimit']!=0
        or config.get('manualTriggerConfig') is not None or config.get('eventTriggerConfig') is not None
        or config.get('scheduleTriggerConfig')!={'replicaCompletionCount':1,'cronExpression':'7,37 * * * *','parallelism':1}):
        raise ValueError('Reviewed schedule, timeout and zero retry must remain exact')
    expected_secret={'name':'monitor-url',
        'keyVaultUrl':'https://ibkrv2stg-auhecwc4itaoo.vault.azure.net/secrets/database-metadata-monitor-url',
        'identity':IDENTITY}
    if config.get('secrets')!=[expected_secret] or config.get('registries')!=[
        {'server':'ibkrv2stgauhecwc4itaoo.azurecr.io','identity':IDENTITY}]:
        raise ValueError('Exact registry and restricted credential binding required')
    containers=props['template'].get('containers',[])
    if len(containers)!=1:
        raise ValueError('One existing monitor container required')
    container=containers[0]
    if (container.get('name')!='metadata-monitor' or container.get('image')!=IMAGE
        or container.get('command')!=['python'] or container.get('resources')!={'cpu':0.25,'memory':'0.5Gi'}
        or container.get('env')!=[{'name':'PORTFOLIO_TENANT_ID','value':'00000000-0000-0000-0000-000000000001'},
                                 {'name':'DATABASE_URL','secretRef':'monitor-url'}]):
        raise ValueError('Container, resources and tenant credentials must remain exact')
    tags=job.get('tags',{})
    wanted={'application':'ibkr-portfolio-platform','purpose':'metadata-shadow-monitor',
            'owner':'portfolio-platform','mode':'shadow'}
    if any(tags.get(k)!=v for k,v in wanted.items()) or set(tags)!=set(wanted)|{'payloadSha256'}:
        raise ValueError('Reviewed monitor ownership and scope required')
    source=decode_bundle(container['args'])
    if tags['payloadSha256']!=hashlib.sha256(source).hexdigest():
        raise ValueError('Existing source bundle is not bound to its recorded digest')
    writable_definition(job)


def invariant(job):
    validate_baseline(job)
    value=writable_definition(job)
    value['properties']['template']['containers'][0]['args']=[]
    value['tags']['payloadSha256']='source-only-mutable'
    # Azure canonicalizes identity keys; values are never writable by this tool.
    value['identity']['userAssignedIdentities']={k.lower():v for k,v in value['identity']['userAssignedIdentities'].items()}
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def source_only_definition(baseline,args):
    validate_baseline(baseline)
    source=decode_bundle(args)
    value=writable_definition(baseline)
    value['properties']['template']['containers'][0]['args']=deepcopy(args)
    value['tags']['payloadSha256']=hashlib.sha256(source).hexdigest()
    return value


def verify_readback(baseline,current,args):
    if invariant(current)!=invariant(baseline):
        raise ValueError('Non-source monitor configuration changed')
    wanted=source_only_definition(baseline,args)
    if (current['properties']['template']['containers'][0]['args']!=args
        or current['tags']['payloadSha256']!=wanted['tags']['payloadSha256']):
        raise ValueError('Exact qualified source-only payload not installed')


def qualify_execution(execution,baseline,args,records,*,now):
    """Require exact native payload, complete recent rows and honest summary."""
    validate_baseline(baseline)
    props=execution['properties']
    if props.get('status')!='Succeeded' or not re.fullmatch(re.escape(JOB)+r'-[a-z0-9]{7,12}',execution.get('name','')):
        raise ValueError('Exact succeeded monitor execution required')
    actual=props['template']['containers']
    wanted=source_only_definition(baseline,args)['properties']['template']['containers'][0]
    if len(actual)!=1 or any(actual[0].get(k)!=wanted[k] for k in ('name','image','command','args')):
        raise ValueError('Native execution differs from the frozen candidate')
    resources=deepcopy(actual[0].get('resources',{}))
    if resources.get('ephemeralStorage')=='':
        resources.pop('ephemeralStorage')
    expected_env=deepcopy(wanted['env'])
    expected_env[1]['secretRef']='cappjob-'+JOB
    if actual[0].get('env')!=expected_env or resources!=wanted['resources']:
        raise ValueError('Native credentials or resource settings changed')
    if not isinstance(now,datetime) or now.utcoffset() is None:
        raise ValueError('Aware verification time required')
    if not isinstance(records,list) or not records or len(records)>100002:
        raise ValueError('Bounded complete monitor log records required')
    markers=[]; indexed={}
    for row in records:
        if not isinstance(row,dict):
            raise ValueError('Structured monitor logs required')
        if row.get('kind')=='complete':
            if row not in markers:markers.append(row)
        elif row.get('kind') in {'risk','finding','benchmark'}:
            if set(row)!={'kind','index','captured_at','value'} or type(row['index']) is not int or row['index']<0:
                raise ValueError('Exact indexed monitor row required')
            if row['index'] in indexed and indexed[row['index']]!=row:
                raise ValueError('Conflicting monitor rows')
            indexed[row['index']]=row
        else:
            raise ValueError('Monitor error or unexpected record')
    if len(markers)!=1:
        raise ValueError('One complete capture required')
    marker=markers[0]
    required={'kind','mode','captured_at','records','evidence_sha256','status',
        'benchmark_checked','benchmark_attention','benchmark_quality_warnings','benchmark_scope',
        'financial_reconciliation','inventory_complete','denied_column_checks','access_check','calendar_version','limits'}
    if set(marker)!=required or marker['mode']!='shadow' or marker['access_check']!='passed' or marker['inventory_complete'] is not True:
        raise ValueError('Complete restricted access and shadow evidence required')
    if type(marker['denied_column_checks']) is not int or marker['denied_column_checks']!=7:
        raise ValueError('All denied-column checks required')
    if (type(marker['records']) is not int or set(indexed)!=set(range(marker['records']))
        or not re.fullmatch(r'[a-f0-9]{64}',marker.get('evidence_sha256',''))):
        raise ValueError('Missing indexed evidence or source-evidence fingerprint')
    captured=datetime.fromisoformat(marker['captured_at'])
    if captured.utcoffset() is None or not now-timedelta(minutes=20)<=captured<=now:
        raise ValueError('Recent aware metadata capture required')
    if any(r['captured_at']!=marker['captured_at'] for r in indexed.values()):
        raise ValueError('Mixed capture rows cannot qualify')
    checks=[r['value'] for r in indexed.values() if r['kind']=='benchmark']
    attention=sum(r['state']!='current_for_scheduled_cycle' or r.get('quality') not in {'pass','warn'} for r in checks)
    warnings=sum(r.get('quality')=='warn' for r in checks)
    expected={'benchmark_checked':len(checks),'benchmark_attention':attention,
        'benchmark_quality_warnings':warnings,'benchmark_scope':'checked' if checks else 'not_checked',
        'financial_reconciliation':'not_certified',
        'status':'needs_review' if attention or any(r['kind']=='finding' for r in indexed.values()) else 'no_findings_in_checked_scope'}
    if any(marker.get(k)!=v or (type(v) is int and type(marker.get(k)) is not int) for k,v in expected.items()):
        raise ValueError('Summary disagrees with complete benchmark/queue/risk detail')
    return {'schema':'wealthryse-monitor-source-qualification-v1','execution':execution['name'],
        'source_sha256':hashlib.sha256(decode_bundle(args)).hexdigest(),'captured_at':marker['captured_at'],
        'marker':marker,'records':len(indexed),'non_source_configuration_sha256':invariant(baseline),
        'financial_acceptance':False,'source_writes':False,'image_security_acceptance':False}
