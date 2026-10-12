"""Bounded private-IP TLS proof from approved admin VM; no DNS/public cutover."""
import hashlib,ipaddress,json,shlex,sys
from pathlib import Path
from deploy_viewer_private_admin_20261008 import request,rid,VM,COMPUTE_API,NET_API
from deploy_upgrade_private_endpoints_20261009 import read as read_endpoints,stable
from run_specialist_gateway_read_verification import retain
from rehearse_ms_worker_image_rollback import restore_protected

ROOT=Path(__file__).resolve().parents[1]/'.work/platform-upgrade-20261009-stage6/private-admin-tls-v1'
VM_ID=rid('Microsoft.Compute/virtualMachines',VM)
NSG=rid('Microsoft.Network/networkSecurityGroups','ibkrv2stg-admin-nsg')
RULE=NSG+'/securityRules/AllowApprovedPrivateEndpointsHttps'
RUN=VM_ID+'/runCommands/upgrade-private-tls-20261009'
PREFIX='PRIVATE_ENDPOINT_TLS '

def save(name,value):
    ROOT.mkdir(exist_ok=True)
    with (ROOT/(name+'.json')).open('x',encoding='utf-8') as f:
        json.dump({**value,'protected_receipt':retain(value,'upgrade-private-tls-'+name)},f,indent=2)

def read(name):
    value=json.loads((ROOT/(name+'.json')).read_bytes());remote,_=restore_protected(value['protected_receipt'])
    if remote!={k:v for k,v in value.items() if k!='protected_receipt'}:raise ValueError('Protected plan differs')
    return value

def vm_boundary():
    vm=request('GET',VM_ID+'?api-version='+COMPUTE_API)
    if vm['identity'].get('principalId')!='f97c7df8-85ab-4a07-91c7-4335e862679f' or vm['properties']['provisioningState']!='Succeeded':
        raise ValueError('Approved private VM identity/readiness differs')
    nics=vm['properties']['networkProfile']['networkInterfaces']
    if len(nics)!=1 or nics[0]['id'].lower()!=rid('Microsoft.Network/networkInterfaces','ibkrv2stg-admin-nic').lower():raise ValueError('VM NIC differs')
    nic=request('GET',nics[0]['id']+'?api-version='+NET_API)
    configs=nic['properties']['ipConfigurations']
    if len(configs)!=1:raise ValueError('Unexpected NIC configuration')
    p=configs[0]['properties']
    if p.get('publicIPAddress') or ipaddress.ip_address(p['privateIPAddress']) not in ipaddress.ip_network('10.50.9.0/27'):
        raise ValueError('VM is not private-only inside approved administration subnet')
    return {'vm':VM_ID,'identity':vm['identity'],'nic':nic['id'],'private_ip':p['privateIPAddress'],'subnet':p['subnet']['id']}

def rules():
    return {r['name']:stable(r['properties']) for r in request('GET',NSG+'?api-version='+NET_API)['properties']['securityRules']}

def main(stage):
    if stage=='prepare':
        if ROOT.exists():raise ValueError('Preserve existing private TLS plan')
        accepted=read_endpoints('verified')
        if not accepted['passed'] or accepted['dns_changed'] or accepted['public_access_changed']:raise ValueError('Additive stage not qualified')
        targets=[]
        for endpoint in accepted['endpoints']:
            if '/ibkr-upgrade-main-' not in endpoint['id']:continue
            live=request('GET',endpoint['id']+'?api-version='+NET_API)
            if live['properties']['customDnsConfigs']!=endpoint['dns']:raise ValueError('Endpoint address drift')
            for dns in endpoint['dns']:
                host=dns['fqdn']
                if not host.endswith(('.azurecr.io','.vault.azure.net','.blob.core.windows.net')):raise ValueError('Unexpected target hostname')
                for address in dns['ipAddresses']:
                    if ipaddress.ip_address(address) not in ipaddress.ip_network('10.50.10.0/27'):raise ValueError('Target outside dedicated endpoint subnet')
                    path='/v2/' if host.endswith('.azurecr.io') and '.data.' not in host else '/'
                    targets.append({'host':host,'ip':address,'path':path})
        if len(targets)!=5:raise ValueError('Expected five main-service private DNS addresses')
        boundary=vm_boundary();before=rules()
        if 'AllowApprovedPrivateEndpointsHttps' in before or any(r['priority']==140 and r['direction']=='Outbound' for r in before.values()):
            raise ValueError('Existing firewall target; do not overwrite')
        rule={'properties':{'priority':140,'direction':'Outbound','access':'Allow','protocol':'Tcp','sourcePortRange':'*',
            'sourceAddressPrefix':boundary['private_ip'],'destinationPortRange':'443','destinationAddressPrefixes':sorted({r['ip'] for r in targets})}}
        save('plan',{'targets':targets,'vm_boundary':boundary,'before_rules':before,'rule':rule,'dns_changed':False,
            'public_access_changed':False,'authorization':'Approved full private-network upgrade; narrowly allow approved admin VM to new private endpoints on TCP 443 only',
            'rollback':{'method':'DELETE','resource':RULE,'condition':'Only if the new rule still exactly matches this intent; preserve all original rules'}})
        print('Prepared five private TLS targets and one narrow outbound rule');return
    plan=read('plan')
    if vm_boundary()!=plan['vm_boundary']:raise ValueError('Private VM boundary changed')
    current=rules();current.pop('AllowApprovedPrivateEndpointsHttps',None)
    if current!=plan['before_rules']:raise ValueError('Existing firewall rules changed')
    if stage=='allow':
        if (ROOT/'rule-intent.json').exists() or request('GET',RULE+'?api-version='+NET_API,missing=True) is not None:raise ValueError('Reconcile existing rule')
        save('rule-intent',{'resource':RULE,'body':plan['rule']})
        ack=request('PUT',RULE+'?api-version='+NET_API,data=plan['rule'])
        save('rule-ack',{'id':ack.get('id'),'state':ack['properties'].get('provisioningState')})
        print('Added exact-VM to exact-private-IP HTTPS rule; public/DNS settings untouched');return
    actual=request('GET',RULE+'?api-version='+NET_API)['properties']
    if actual.get('provisioningState')!='Succeeded' or any(actual.get(k)!=v for k,v in plan['rule']['properties'].items()):raise ValueError('Firewall rule differs')
    if stage=='start':
        if (ROOT/'run-intent.json').exists() or request('GET',RUN+'?api-version='+COMPUTE_API,missing=True) is not None:raise ValueError('Preserve earlier run command')
        code="import json,subprocess\ntargets="+repr(plan['targets'])+"\nresults=[]\n"
        code+="for target in targets:\n r=subprocess.run(['curl','--silent','--noproxy','*','--connect-timeout','5','--max-time','12','--resolve',target['host']+':443:'+target['ip'],'--output','/dev/null','--write-out','%{http_code} %{remote_ip} %{ssl_verify_result}','https://'+target['host']+target['path']],capture_output=True,text=True)\n fields=r.stdout.split()\n results.append({**target,'exit_code':r.returncode,'status':int(fields[0]) if len(fields)==3 and fields[0].isdigit() else None,'remote_ip':fields[1] if len(fields)==3 else None,'certificate_verification':fields[2] if len(fields)==3 else None})\n"
        code+="print('PRIVATE_ENDPOINT_TLS '+json.dumps({'results':results,'credentials_read':False,'authenticated_access_tested':False,'dns_changed':False}))\n"
        body={'location':'eastasia','properties':{'source':{'script':'python3 -c '+shlex.quote(code)},'asyncExecution':True,'timeoutInSeconds':150,'treatFailureAsDeploymentFailure':True}}
        save('run-intent',{'resource':RUN,'body':body,'script_sha256':hashlib.sha256(code.encode()).hexdigest()})
        ack=request('PUT',RUN+'?api-version='+COMPUTE_API,data=body);save('run-ack',{'id':ack.get('id')})
        print('Started private-IP certificate-verified TLS probe; no credentials or DNS changes');return
    if stage!='read':raise ValueError('Unknown stage')
    intent=read('run-intent');run=request('GET',RUN+'?api-version='+COMPUTE_API+'&$expand=instanceView');view=run['properties'].get('instanceView',{})
    if view.get('executionState')!='Succeeded':print(json.dumps({'state':view.get('executionState'),'passed':False}));return
    if view.get('exitCode')!=0 or run['properties']['source']!=intent['body']['properties']['source']:raise ValueError('Probe execution differs')
    lines=[r[len(PREFIX):] for r in view.get('output','').splitlines() if r.startswith(PREFIX)]
    if len(lines)!=1 or len(lines[0])>4096:raise ValueError('Bounded structured evidence required')
    result=json.loads(lines[0]);rows=result['results']
    if [{k:r[k] for k in ('host','ip','path')} for r in rows]!=plan['targets']:raise ValueError('TLS targets differ')
    passed=all(r['exit_code']==0 and r['remote_ip']==r['ip'] and r['certificate_verification']=='0' and r['status'] in (200,400,401,403,404) for r in rows)
    save('result',{'passed':passed,'evidence':result,'private_authenticated_acceptance_complete':False,'public_cutover_permitted':False})
    print(json.dumps({'private_tls_passed':passed,'targets':len(rows),'authenticated_acceptance':False,'dns_or_public_cutover':False}))

if __name__=='__main__':main(sys.argv[1])
