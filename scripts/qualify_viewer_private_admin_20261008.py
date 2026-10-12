"""New-VM-only access/tool acceptance; no production credentials or public cuts.

Management-plane host-key evidence pins the real Bastion SSH host. SSH must use
Entra ID and the existing owner, with host verification and passwordless sudo.
Run-command and SSH output is allow-listed structured evidence, never raw logs.
Local Docker proof is not registry push/cold-pull or full release qualification.
"""
import argparse,hashlib,json,re,shlex,subprocess
from pathlib import Path
from deploy_viewer_private_admin_20261008 import ROOT as INFRA,VM,BASTION,OWNER,rid,request,COMPUTE_API,verify_boundaries
from audit_azure_runtime import AZ,RG,az
from qualify_viewer_database_recovery_20261008 import read,save

ROOT=INFRA/'qualification'
PREFIX='PRIVATE_ADMIN_EVIDENCE '
VM_ID=rid('Microsoft.Compute/virtualMachines',VM)
HOST_CODE=r'''import hashlib,json,pathlib,socket,subprocess
key=pathlib.Path('/etc/ssh/ssh_host_ed25519_key.pub').read_text().split()
config=dict(line.split(' ',1) for line in subprocess.check_output(['/usr/sbin/sshd','-T'],text=True).splitlines() if ' ' in line)
host='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com'
addresses=sorted({row[4][0] for row in socket.getaddrinfo(host,5432,type=socket.SOCK_STREAM)})
print('PRIVATE_ADMIN_EVIDENCE '+json.dumps({'kind':'host','ssh_host_key':' '.join(key[:2]),'postgres_dns_addresses':addresses,'password_authentication':config.get('passwordauthentication'),'pubkey_authentication':config.get('pubkeyauthentication')}))
'''
LOGIN_CODE=r'''import hashlib,json,os,subprocess
print('PRIVATE_ADMIN_EVIDENCE '+json.dumps({'kind':'login','uid':os.getuid(),'passwordless_admin':subprocess.run(['sudo','-n','true'],capture_output=True).returncode==0,'hostname_sha256':hashlib.sha256(os.uname().nodename.encode()).hexdigest()}))
'''
TOOLS=r'''#!/bin/bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y ca-certificates curl gnupg lsb-release docker.io python3-venv git
install -d -m 0755 /etc/apt/keyrings
curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor --yes -o /etc/apt/keyrings/microsoft.gpg
chmod 0644 /etc/apt/keyrings/microsoft.gpg
printf 'Types: deb\nURIs: https://packages.microsoft.com/repos/azure-cli/\nSuites: noble\nComponents: main\nArchitectures: amd64\nSigned-by: /etc/apt/keyrings/microsoft.gpg\n' > /etc/apt/sources.list.d/azure-cli.sources
apt-get update -qq
apt-get install -y azure-cli
systemctl enable --now docker
python3 - <<'PY'
import hashlib,json,pathlib,subprocess,tempfile
versions=json.loads(subprocess.check_output(['az','version','-o','json'],text=True))
with tempfile.TemporaryDirectory(prefix='ibkr-private-build-proof-') as directory:
    target=pathlib.Path(directory)
    (target/'Dockerfile').write_text('FROM scratch\nCOPY evidence.txt /evidence.txt\n')
    (target/'evidence.txt').write_text('Private administration local build proof. No portfolio data.\n')
    subprocess.run(['docker','build','--network=none','-t','ibkr-private-build-proof:20261008',directory],check=True,capture_output=True)
image=json.loads(subprocess.check_output(['docker','image','inspect','ibkr-private-build-proof:20261008'],text=True))[0]['Id']
print('PRIVATE_ADMIN_EVIDENCE '+json.dumps({'kind':'tools','azure_cli_version':versions['azure-cli'],'docker_version':subprocess.check_output(['docker','version','--format','{{.Server.Version}}'],text=True).strip(),'local_build_image_id':image,'registry_push_tested':False,'source_data_in_build':False}))
PY
'''

def parse_evidence(output,kind):
    matches=[line[len(PREFIX):] for line in output.splitlines() if line.startswith(PREFIX)]
    if len(matches)!=1 or len(matches[0])>4096:raise ValueError('One bounded structured evidence line required; raw output omitted')
    value=json.loads(matches[0])
    allowed={'host':{'kind','ssh_host_key','postgres_dns_addresses','password_authentication','pubkey_authentication'},'login':{'kind','uid','passwordless_admin','hostname_sha256'},'tools':{'kind','azure_cli_version','docker_version','local_build_image_id','registry_push_tested','source_data_in_build'}}
    if value.get('kind')!=kind or set(value)!=allowed[kind]:raise ValueError('Unknown structured evidence fields')
    if kind=='host':
        import ipaddress
        if not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/=]{60,100}',value['ssh_host_key']) or value['password_authentication']!='no' or value['pubkey_authentication']!='yes':raise ValueError('Host key or actual SSH configuration differs')
        if not value['postgres_dns_addresses'] or any(ipaddress.ip_address(x) not in ipaddress.ip_network('10.50.4.0/28') for x in value['postgres_dns_addresses']):raise ValueError('Database did not resolve inside existing private subnet')
    elif kind=='login':
        if type(value['uid']) is not int or value['uid']<=0 or value['passwordless_admin'] is not True or not re.fullmatch('[0-9a-f]{64}',value['hostname_sha256']):raise ValueError('Real non-root owner admin SSH login required')
    elif not re.fullmatch('sha256:[0-9a-f]{64}',value['local_build_image_id']) or value['registry_push_tested'] is not False or value['source_data_in_build'] is not False:raise ValueError('Local-only build proof differs')
    return value

def gate():
    infra=read(INFRA,'infrastructure-qualified');plan=read(INFRA,'plan')
    if 'extension_repair_plan_sha256' in infra:
        from repair_viewer_private_admin_extension_20261008 import verify_boundaries as semantic_boundaries
        semantic_boundaries(plan)
    else:verify_boundaries(plan)
    if 'forward_repair_plan_sha256' in infra:
        from repair_viewer_private_admin_20261008 import ROOT as REPAIR,verify_new_network
        repair=read(REPAIR,'plan')
        if hashlib.sha256((REPAIR/'plan.json').read_bytes()).hexdigest()!=infra['forward_repair_plan_sha256']:raise ValueError('Accepted repair binding differs')
        verify_new_network(repair['template'])
    if 'extension_repair_plan_sha256' in infra:
        from repair_viewer_private_admin_extension_20261008 import ROOT as EXT_REPAIR
        import verify_viewer_private_admin_extension_20261008 as readback
        extension=read(EXT_REPAIR,'plan');read(EXT_REPAIR,'qualified')
        if hashlib.sha256((EXT_REPAIR/'plan.json').read_bytes()).hexdigest()!=infra['extension_repair_plan_sha256'] or hashlib.sha256(Path(readback.__file__).read_bytes()).hexdigest()!=infra.get('readback_verifier_sha256'):raise ValueError('Accepted extension repair/readback verifier differs')
        readback.verify_retained(extension,repair)
    if infra['vm']!=VM_ID or infra['vm_public_ip'] or not infra['owner_only_vm_login'] or az('ad','signed-in-user','show','--query','id')!=OWNER:raise ValueError('Exact approved new VM and owner required')
    vm=request('GET',VM_ID+'?api-version='+COMPUTE_API)
    if vm['properties']['provisioningState']!='Succeeded' or vm['properties']['osProfile']['computerName']!='ibkr-private-admin':raise ValueError('VM identity/readiness drift')
    ROOT.mkdir(exist_ok=True);return vm

def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=('prepare','dispatch','inspect','login'));parser.add_argument('--kind',choices=('host','tools'));args=parser.parse_args();gate()
    if args.stage=='login':
        raise ValueError('Use qualify_viewer_private_admin_login_20261008.py: the installed Bastion CLI SSH wrapper prepends unsafe host-check options and does not propagate the SSH exit code')
    if args.kind is None:raise ValueError('Select one bounded host/tools command')
    name='private-admin-'+args.kind+'-20261008';path=VM_ID+'/runCommands/'+name;api='?api-version='+COMPUTE_API
    if args.stage=='prepare':
        if (ROOT/(args.kind+'-intent.json')).exists() or request('GET',path+api,missing=True) is not None:raise ValueError('Preserve earlier run-command')
        code=TOOLS if args.kind=='tools' else 'python3 -c '+shlex.quote(HOST_CODE)
        body={'location':'eastasia','properties':{'source':{'script':code},'asyncExecution':True,'timeoutInSeconds':1800 if args.kind=='tools' else 180,'treatFailureAsDeploymentFailure':True}}
        save(ROOT,args.kind+'-intent',{'target':path,'body':body,'script_sha256':hashlib.sha256(code.encode()).hexdigest(),'production_mutations':0,'credentials_read':0});print('Prepared new-VM-only '+args.kind+' command');return
    intent=read(ROOT,args.kind+'-intent')
    if intent['target']!=path:raise ValueError('Exact new VM run-command scope required')
    if args.stage=='dispatch':
        if (ROOT/(args.kind+'-dispatch-intent.json')).exists():raise ValueError('Inspect rather than repeat run-command')
        save(ROOT,args.kind+'-dispatch-intent',{'intent_sha256':hashlib.sha256((ROOT/(args.kind+'-intent.json')).read_bytes()).hexdigest()})
        response=request('PUT',path+api,data=intent['body']);save(ROOT,args.kind+'-dispatch',{'id':response.get('id'),'state':response['properties'].get('provisioningState')});print('Dispatched new-VM-only '+args.kind+' command');return
    current=request('GET',path+api+'&$expand=instanceView');view=current['properties'].get('instanceView',{})
    print(json.dumps({'kind':args.kind,'execution_state':view.get('executionState'),'exit_code':view.get('exitCode')}),flush=True)
    if view.get('executionState')!='Succeeded':return
    if view.get('exitCode')!=0 or current['properties']['source']['script']!=intent['body']['properties']['source']['script']:raise ValueError('Run-command exit/script differs; raw output omitted')
    evidence=parse_evidence(view.get('output',''),args.kind)
    if not (ROOT/(args.kind+'-qualified.json')).exists():save(ROOT,args.kind+'-qualified',{'status':'passed','evidence':evidence,'production_mutations':0,'credentials_read':0,'public_access_restriction_allowed':False})
    print(json.dumps({'status':'passed','kind':args.kind,'public_access_restriction_allowed':False}))

if __name__=='__main__':main()
