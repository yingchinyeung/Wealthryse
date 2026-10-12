"""Read health and rejection responses from the unchanged internal broker route."""
import hashlib
import json
import sys
from urllib.parse import urlsplit
from release_upgrade_broker_security_20261010 import OUT, APP, API
from control_upgrade_broker_http_20261010 import JOB, PATH, save, candidate
from control_upgrade_legacy_recovery_probe_20261009 import protected
from deploy_viewer_private_admin_20261008 import request
from deploy_platform_access_correction_20261009 import invariant
from deploy_ms_presentation_worker_release import settings_invariant
from verify_installed_ms_worker_release import corrected_template
from audit_azure_runtime import az
import deploy_t_runtime_release_20261009 as runtime
runtime.request = request
FOLDER = OUT / 'installed-http-v1'
CODE = '''import json,urllib.request,urllib.error
def call(path,method='GET',body=None):
 req=urllib.request.Request(TARGET+path,method=method,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
 try:
  with urllib.request.urlopen(req,timeout=15) as response:return response.status,response.read()
 except urllib.error.HTTPError as error:return error.code,error.read()
status,body=call('/health')
if status!=200 or json.loads(body)!={'status':'ready','capability':'set-only-flex-secrets'}:raise ValueError('Internal readiness failed')
checks=['internal_health_ready']
for path in ('/docs','/openapi.json'):
 if call(path)[0]!=404:raise ValueError('Documentation unexpectedly public')
 checks.append(path+'_disabled')
status,body=call('/v1/credentials','POST',{'secret_name':'ibkr-flex-source-'+'c'*32,'token':'synthetic-rejection-probe'})
if status!=403 or b'synthetic-rejection-probe' in body:raise ValueError('Unauthenticated request not safely rejected')
checks.append('unauthenticated_credential_write_denied')
print('BROKER_INSTALLED_PROOF '+json.dumps({'passed':True,'checks':checks,'secret_writes':0,'database_connections':0,'credentials_supplied':False}),flush=True)
'''


def main(stage):
    image = candidate()
    deployed = protected(OUT / 'deployment-v1/verified.json')
    plan = protected(OUT / 'deployment-v1/plan.json')
    app = request('GET', APP + API)
    if not deployed['passed'] or deployed['image'] != image or app['properties']['template']['containers'][0]['image'] != image or invariant(app) != plan['invariant']:
        raise ValueError('Verified installed broker required')
    job = request('GET', PATH + API)
    if job['properties']['environmentId'].lower() != app['properties']['environmentId'].lower():
        raise ValueError('Existing same-environment caller required')
    if stage == 'start':
        if FOLDER.exists(): raise ValueError('Reconcile existing installed test')
        runtime.idle(JOB)
        ingress = app['properties']['configuration']['ingress']
        host = ingress['fqdn']
        if ingress['external'] or not host.startswith('ibkrv2stg-credential-broker.internal.') or not host.endswith('.eastasia.azurecontainerapps.io'):
            raise ValueError('Exact internal East Asia broker route required')
        code = 'TARGET=' + repr('https://' + host) + '\n' + CODE
        template = corrected_template(job, code)
        template['containers'][0]['env'] = []
        FOLDER.mkdir()
        save(FOLDER, 'intent', {'template': template, 'job_invariant': settings_invariant(job),
                              'source_sha256': hashlib.sha256(code.encode()).hexdigest(), 'broker_image': image,
                              'scope': 'Internal health/docs GET and one synthetic unauthenticated POST expected to be rejected; no credentials supplied'})
        ack = request('POST', PATH + '/start' + API, data=template)
        save(FOLDER, 'dispatch', {'execution': ack['name']})
        print(json.dumps({'execution': ack['name']})); return
    if stage != 'read': raise ValueError('Unknown stage')
    intent = protected(FOLDER / 'intent.json'); execution = protected(FOLDER / 'dispatch.json')['execution']
    if settings_invariant(job) != intent['job_invariant']: raise ValueError('Caller settings drift')
    run = request('GET', PATH + '/executions/' + execution + API)
    actual = run['properties']['template']['containers'][0]; expected = intent['template']['containers'][0]
    if any(actual.get(k) != expected.get(k) for k in ('image', 'command', 'args')) or actual.get('env', []):
        raise ValueError('Executed installed test differs')
    state = run['properties']['status']
    if state not in ('Succeeded', 'Failed'):
        print(json.dumps({'state': state})); return
    prefix = 'BROKER_INSTALLED_PROOF '
    query = "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '" + execution + "-' | where Log_s startswith '" + prefix + "' | project Result=parse_json(substring(Log_s," + str(len(prefix)) + '))'
    rows = az('monitor', 'log-analytics', 'query', '-w', '5264a124-10cd-45f6-93a5-6d65c37e9bfb', '--analytics-query', query)
    if len(rows) != 1:
        print(json.dumps({'state': state, 'proof_available': False})); return
    proof = json.loads(rows[0]['Result'])
    passed = state == 'Succeeded' and proof['passed'] and set(proof['checks']) == {'internal_health_ready', '/docs_disabled', '/openapi.json_disabled', 'unauthenticated_credential_write_denied'} and proof['secret_writes'] == proof['database_connections'] == 0 and not proof['credentials_supplied']
    value = {'passed': passed, 'image': image, 'execution': execution, 'proof': proof,
             'broker_settings_and_auth_preserved': True, 'caller_settings_preserved': True, 'full_upgrade_complete': False}
    if not (FOLDER / 'result.json').exists(): save(FOLDER, 'result', value)
    print(json.dumps({'passed': passed, 'checks': len(proof['checks'])}))


if __name__ == '__main__': main(sys.argv[1])
