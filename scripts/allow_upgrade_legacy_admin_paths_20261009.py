"""One exact-VM to three exact private-IP HTTPS rule; no broader VNet access."""
import json,sys
from pathlib import Path
from deploy_viewer_private_admin_20261008 import request
from qualify_upgrade_private_tls_20261009 import vm_boundary,rules,NSG,NET_API
from add_upgrade_legacy_private_paths_20261009 import load as endpoint_evidence
from run_specialist_gateway_read_verification import retain
from rehearse_ms_worker_image_rollback import restore_protected

OUT=Path(__file__).resolve().parents[1]/'.work/platform-upgrade-20261009-stage10/legacy-admin-rule-v1'
NAME='AllowLegacyPrivateEndpointsHttps';RULE=NSG+'/securityRules/'+NAME;API='?api-version='+NET_API
def save(name,value):
    with (OUT/(name+'.json')).open('x',encoding='utf-8') as f:json.dump({**value,'protected_receipt':retain(value,'upgrade-legacy-admin-'+name)},f,indent=2)
def load(name):
    value=json.loads((OUT/(name+'.json')).read_bytes());remote,_=restore_protected(value['protected_receipt'])
    if remote!={k:v for k,v in value.items() if k!='protected_receipt'}:raise ValueError('Protected evidence differs')
    return value
def main(stage):
    boundary=vm_boundary();current=rules()
    if stage=='plan':
        if OUT.exists() or NAME in current or any(r['priority']==145 and r['direction']=='Outbound' for r in current.values()):raise ValueError('Preserve existing rule or priority')
        addresses=set()
        for kind in ('blob','registry'):
            proof=endpoint_evidence(kind+'-verified')
            if not proof['passed'] or proof['dns_changed'] or proof['public_access_changed']:raise ValueError('Additive endpoint not qualified')
            for d in proof['dns']:addresses.update(d['ipAddresses'])
        if addresses!={'10.50.10.9','10.50.10.10','10.50.10.11'}:raise ValueError('Qualified private addresses changed')
        body={'properties':{'priority':145,'direction':'Outbound','access':'Allow','protocol':'Tcp','sourcePortRange':'*','sourceAddressPrefix':boundary['private_ip'],'destinationPortRange':'443','destinationAddressPrefixes':sorted(addresses)}}
        OUT.mkdir()
        save('plan',{'vm_boundary':boundary,'before_rules':current,'body':body,'scope':'Only approved private build VM to exact newly qualified private endpoint addresses on TCP 443','rollback':{'method':'DELETE','resource':RULE,'condition':'Only this newly created exact-matching rule if no build is active'}})
        print('Protected exact-VM/exact-private-IP HTTPS plan');return
    plan=load('plan');original={k:v for k,v in current.items() if k!=NAME}
    if boundary!=plan['vm_boundary'] or original!=plan['before_rules']:raise ValueError('Existing VM/network policy drift')
    if stage=='apply':
        if NAME in current or (OUT/'apply-intent.json').exists():raise ValueError('Reconcile existing rule')
        save('apply-intent',{'body':plan['body'],'resource':RULE})
        r=request('PUT',RULE+API,data=plan['body']);save('apply-ack',{'id':r['id'],'state':r['properties'].get('provisioningState')});print('Narrow private-build HTTPS path added');return
    if stage!='verify':raise ValueError('Unknown stage')
    actual=request('GET',RULE+API)['properties']
    if actual['provisioningState']!='Succeeded' or any(actual.get(k)!=v for k,v in plan['body']['properties'].items()):raise ValueError('Rule differs')
    save('verified',{'passed':True,'vm_boundary':boundary,'addresses':plan['body']['properties']['destinationAddressPrefixes'],'existing_rules_preserved':True})
    print('Exact private build HTTPS rule verified; existing rules preserved')
if __name__=='__main__':main(sys.argv[1])
