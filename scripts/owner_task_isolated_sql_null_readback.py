"""Same installed named job; only two exact null Azure descriptors are removed.

Frozen 766-source preflight, job install and original failed attempt are retained.
This adapter adds no installation, grant, retargeting or retry authority. START
uses the original named operation and reservation, with fresh protected review.
"""
import argparse
import json
import re

import deploy_owner_task_isolated_sql_named as prior

ROOT, FOLDER, worker = prior.ROOT, prior.FOLDER, prior.worker
ADDITIONS = frozenset(('scripts/owner_task_isolated_sql_null_readback.py',
    'tests/test_owner_task_isolated_sql_null_readback.py'))
PREFLIGHT_SHA = 'bcb78f062cf6aeaa868b07e743c843015732f79629236129b8c3fbadef442b5c'
DIAGNOSIS_SHA = '5349b4cbe5cb25698640b03bbea57fa26809fc8f1f3f1dc1617482519e2cabfd'


def require(value, code):
    if not value: raise ValueError(code)


def validate_sources(before, after):
    require(type(before) is dict and len(before) == 766 and type(after) is dict
        and set(after) == set(before) | ADDITIONS and not set(before) & ADDITIONS
        and all(after[k] == v for k,v in before.items())
        and all(type(k) is str and type(v) is str and re.fullmatch('[0-9a-f]{64}',v) for k,v in after.items()),
        'isolated_sql_null_sources_invalid')


def validate_job(value, plan, identity):
    shadow = prior.compatibility.copy(value)
    template = shadow.get('properties',{}).get('template')
    require(type(template) is dict, 'isolated_sql_null_template_invalid')
    for field in ('initContainers','volumes'):
        if field in template:
            require(template[field] is None, 'isolated_sql_null_template_invalid')
            del template[field]
    result = prior.validate_job(shadow,plan,identity)
    return dict(result,native_job_sha256=prior.digest(value),detached_shadow_sha256=prior.digest(shadow),
        only_null_execution_descriptors_removed=shadow != value)


def origin():
    raw,pf=prior.load('preflight.json'); diagnosis_raw,diagnosis=prior.load('job-template-diagnosis.json')
    require(raw['protected_receipt']['sha256']==PREFLIGHT_SHA
        and pf['status']=='prepared_private_sql_named_trial' and pf['operation_id']==worker.OPERATION
        # The independently protected exact named preflight already binds the
        # fully verified immutable predecessor chain. Validate its fixed roots;
        # do not reread every ancestor on each current-job observation.
        and pf['origin_receipts']['preflight']['sha256']==prior.reviewed.PREFLIGHT_SHA
        and pf['origin_receipts']['readback']['sha256']=='0c4286fba5ab1649034af1dbf82da913d110c52e87324c3370b41952bcd62482'
        and pf['origin_receipts']['validation']['sha256']=='19ff1341852388478a95993d0cb4d1eec13fdde18f1bede8fc593417f6059d18'
        and diagnosis_raw['protected_receipt']['sha256']==DIAGNOSIS_SHA
        and diagnosis['preflight_receipt']==raw['protected_receipt'] and diagnosis['sql_started'] is False,
        'isolated_sql_null_origin_invalid')
    template=diagnosis['native']['properties']['template']
    require(all(k in template and template[k] is None for k in ('initContainers','volumes')),
        'isolated_sql_null_origin_invalid')
    return raw,pf,diagnosis_raw


def live(client,pf):
    prior.host.checked_owner()
    require(prior.baseline()==pf['baseline'] and prior.host.observe(prior.host.Transport())==pf['host']
        and client.call('GET',prior.predecessor.JOB,'2025-07-01',missing=True) is None,
        'isolated_sql_null_live_drift')
    identity=prior.live_identity(client.call('GET',prior.MI,'2023-01-31'))
    roles=prior.roles(client,identity['principalId'],['acr','secret'])
    native=client.call('GET',prior.JOB,'2025-07-01')
    checked=validate_job(native,pf['plan'],identity)
    return dict(identity=identity,role_inventory=roles,native_job=native,checked=checked)


def prepare(client):
    require(not (FOLDER/'null-readback-review.json').exists()
        and not any((FOLDER/k).exists() for k in ('job-accepted.json','start-intent.json','start-ack.json')),
        'isolated_sql_null_review_consumed')
    raw,pf,diagnosis=origin(); before=prior.host.sources(); validate_sources(pf['sources'],before)
    observed=live(client,pf)
    require(observed['checked']['native_job_sha256']==prior.digest(diagnosis['native']), 'isolated_sql_null_native_drift')
    checks=prior.reviewed.run_local_checks()
    require(prior.host.sources()==before and live(client,pf)==observed, 'isolated_sql_null_review_drift')
    return prior.save(dict(status='two_null_descriptors_reviewed',operation_id=worker.OPERATION,sources=before,
        preflight_receipt=raw['protected_receipt'],diagnosis_receipt=diagnosis['protected_receipt'],
        live=observed,local_checks=checks,checked_at_utc=prior.now(),new_dispatch_scope=False,
        step_2_complete=False),'null-readback-review.json')


def guards(client):
    raw,pf,diagnosis=origin(); review_raw,review=prior.load('null-readback-review.json')
    require(review['status']=='two_null_descriptors_reviewed' and review['operation_id']==worker.OPERATION
        and review['preflight_receipt']==raw['protected_receipt']
        and review['diagnosis_receipt']==diagnosis['protected_receipt']
        and review['new_dispatch_scope'] is False and review['step_2_complete'] is False,
        'isolated_sql_null_review_invalid')
    validate_sources(pf['sources'],review['sources'])
    require(prior.host.sources()==review['sources'],'isolated_sql_null_source_drift')
    observed=live(client,pf)
    require(observed==review['live'],'isolated_sql_null_live_drift')
    return raw,pf,review_raw,observed


def accept_job(client):
    require(not (FOLDER/'job-accepted.json').exists(),'isolated_sql_null_acceptance_consumed')
    raw,pf,review,observed=guards(client)
    ack_raw,ack=prior.load('job-ack.json'); intent_raw,intent=prior.load('job-intent.json')
    require(ack['intent_receipt']==intent_raw['protected_receipt']
        and intent['preflight_receipt']==raw['protected_receipt'] and intent['operation_id']==worker.OPERATION
        and intent['stage']=='job' and ack['stage']=='job' and ack['attempt_consumed'] is True,
        'isolated_sql_null_install_chain_invalid')
    return prior.save(dict(status='private_sql_effect_verified',stage='job',operation_id=worker.OPERATION,
        preflight_receipt=raw['protected_receipt'],null_review_receipt=review['protected_receipt'],
        ack_receipt=ack_raw['protected_receipt'],identity=observed['identity'],role_inventory=observed['role_inventory'],
        native_verification=observed['checked'],checked_at_utc=prior.now(),step_2_complete=False),'job-accepted.json')


def start(client):
    require(not any((FOLDER/k).exists() for k in ('start-intent.json','start-ack.json')),
        'isolated_sql_null_start_consumed_inspect_only')
    raw,pf,review,_=guards(client); accepted_raw,accepted=prior.load('job-accepted.json')
    require(accepted['status']=='private_sql_effect_verified' and accepted['operation_id']==worker.OPERATION
        and accepted['preflight_receipt']==raw['protected_receipt']
        and accepted['null_review_receipt']==review['protected_receipt'],'isolated_sql_null_install_required')
    inventory=client.call('GET',prior.JOB+'/executions','2025-07-01')
    require(inventory.get('value')==[] and inventory.get('nextLink') in (None,''),'isolated_sql_execution_already_exists')
    method,path,api,body=prior.effect('start',pf['plan'])
    value=dict(operation_id=worker.OPERATION,stage='start',preflight_receipt=raw['protected_receipt'],
        null_review_receipt=review['protected_receipt'],job_acceptance_receipt=accepted_raw['protected_receipt'],
        method=method,path=path,api=api,body=body,automatic_retry=False)
    intent=prior.save(dict(value,reservation=prior.reserve('start',value)),'start-intent.json')
    guards(client)
    inventory=client.call('GET',prior.JOB+'/executions','2025-07-01')
    require(inventory.get('value')==[] and inventory.get('nextLink') in (None,''),'isolated_sql_execution_already_exists')
    ack=client.call(method,path,api,body=body,stage='start',plan=pf['plan'])
    return prior.save(dict(status='private_sql_effect_acknowledged_inspect_required',stage='start',
        operation_id=worker.OPERATION,intent_receipt=intent['protected_receipt'],raw_ack=ack,
        attempt_consumed=True,automatic_retry=False,checked_at_utc=prior.now()),'start-ack.json')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=('prepare','accept-job','start'));args=parser.parse_args()
    try:
        client=prior.Transport()
        result=prepare(client) if args.command=='prepare' else accept_job(client) if args.command=='accept-job' else start(client)
        print(json.dumps({k:result[k] for k in ('status','stage','protected_receipt') if k in result}),flush=True)
    except Exception as error:
        code=str(error);code=code if re.fullmatch('isolated_sql_[a-z_]+',code) else 'isolated_sql_null_unaccepted_inspect_only'
        print(json.dumps(dict(status='unaccepted',code=code,automatic_retry=False)),flush=True)
        raise SystemExit(1) from None


if __name__=='__main__':main()
