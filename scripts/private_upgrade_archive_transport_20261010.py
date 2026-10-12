"""Host-pinned Bastion/SSH route for protected audit storage, credentials local.

Fixed blob endpoint only. No grants, remote credentials, persistent DNS/proxy
or public access changes. All blob responses must traverse the local forward.
"""
import contextlib,json,socket,subprocess,tempfile,time
from pathlib import Path
from urllib.parse import urlsplit
import requests
import qualify_viewer_private_admin_login_20261008 as login
from qualify_upgrade_private_tls_20261009 import vm_boundary
from control_upgrade_legacy_recovery_probe_20261009 import protected
from deploy_viewer_private_admin_20261008 import request,rid

HOST='ibkrv2stgauhecwc4itaoo.blob.core.windows.net';IP='10.50.10.6'
ROOT=Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


def wait_port(port,process):
    deadline=time.monotonic()+45
    while time.monotonic()<deadline and process.poll() is None:
        try:
            with socket.create_connection(('127.0.0.1',port),timeout=1):return
        except OSError:time.sleep(.25)
    raise RuntimeError('Private transport failed to start; raw authentication output not retained')


def stop(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=10)


def routed_lookup(original,port):
    def lookup(host,service,*args,**kwargs):
        if host==HOST:
            if str(service)!='443':raise ValueError('Archive tunnel permits HTTPS only')
            return original('127.0.0.1',port,*args,**kwargs)
        return original(host,service,*args,**kwargs)
    return lookup


def response_guard(original,port,observed):
    def send(adapter,request,stream=False,timeout=None,verify=True,cert=None,proxies=None):
        private=urlsplit(request.url).hostname==HOST
        if private and (urlsplit(request.url).scheme!='https' or urlsplit(request.url).port not in (None,443) or verify is False or any((proxies or {}).values())):
            raise ValueError('No insecure connection or external proxy permitted')
        response=original(adapter,request,stream=stream,timeout=timeout,verify=verify,cert=cert,proxies=proxies)
        if private:
            connection=getattr(response.raw,'_connection',None)
            sock=getattr(connection,'sock',None)
            if sock is None or sock.getpeername()[:2]!=('127.0.0.1',port):
                response.close();raise ValueError('Blob response bypassed fixed private SSH route')
            observed['private_https_responses']+=1
        return response
    return send


@contextlib.contextmanager
def archive_tunnel():
    # Bootstrap from the already pinned local SSH evidence, then verify its
    # exact protected versions through the private route before yielding.
    boundary=vm_boundary();host=json.loads((login.q.ROOT/'host-qualified.json').read_bytes())
    accepted=json.loads((login.q.ROOT/'login-qualified.json').read_bytes())
    if accepted.get('status')!='passed' or accepted.get('host_key_pinned') is not True or accepted.get('owner')!=login.q.OWNER:
        raise ValueError('Qualified owner SSH path required')
    user=login.az('ad','signed-in-user','show','--query','{id:id,name:userPrincipalName}')
    if user['id'].lower()!=login.q.OWNER.lower():raise ValueError('Current user differs from approved owner')
    known=login.q.ROOT/'known-hosts'
    if known.read_text()!=login.q.VM+' '+host['evidence']['ssh_host_key']+'\n':raise ValueError('Pinned host key changed')
    ep=request('GET',rid('Microsoft.Network/privateEndpoints','ibkr-upgrade-main-blob-pe')+'?api-version=2024-05-01')['properties']
    if ep.get('provisioningState')!='Succeeded' or ep['subnet']['id'].lower()!=rid('Microsoft.Network/virtualNetworks','ibkrv2stg-vnet').lower()+'/subnets/private-endpoints-upgrade':raise ValueError('Private blob endpoint moved')
    if len(ep['privateLinkServiceConnections'])!=1 or ep['privateLinkServiceConnections'][0]['properties']['privateLinkServiceId'].lower()!=rid('Microsoft.Storage/storageAccounts','ibkrv2stgauhecwc4itaoo').lower():raise ValueError('Wrong endpoint target')
    nic=request('GET',ep['networkInterfaces'][0]['id']+'?api-version=2024-05-01')
    if [v['properties']['privateIPAddress'] for v in nic['properties']['ipConfigurations']]!=[IP]:raise ValueError('Private blob IP changed')
    bastion=request('GET',rid('Microsoft.Network/bastionHosts',login.q.BASTION)+'?api-version=2024-05-01')
    if bastion['properties'].get('enableTunneling') is not True:raise ValueError('Existing Bastion tunnel unavailable')
    scratch=ROOT/'.work/platform-upgrade-20261010-stage28';scratch.mkdir(exist_ok=True)
    transport=ssh=None;lookup=socket.getaddrinfo;send=requests.adapters.HTTPAdapter.send
    try:
        with tempfile.TemporaryDirectory(prefix='private-archive-entra-',dir=scratch) as folder:
            target=Path(folder).resolve()
            if target.parent!=scratch.resolve():raise ValueError('Credential directory outside scope')
            cert=target/'id_rsa-aadcert.pub';private=target/'id_rsa'
            result=subprocess.run([login.AZ,'-m','azure.cli','ssh','cert','--file',str(cert),'--only-show-errors'],capture_output=True,text=True,timeout=90)
            if result.returncode or not cert.exists() or not private.exists():raise RuntimeError('Owner SSH certificate unavailable')
            result=subprocess.run([str(login.KEYGEN),'-L','-f',str(cert)],capture_output=True,text=True,timeout=15)
            names=login.principals(result.stdout)
            if result.returncode or not login.owner_certificate_matches(names,user['name']):raise ValueError('Certificate principal differs')
            port=free_port();forward=free_port()
            transport=subprocess.Popen([login.AZ,'-m','azure.cli','network','bastion','tunnel','-n',login.q.BASTION,'-g',login.RG,
                '--target-resource-id',login.q.VM_ID,'--resource-port','22','--port',str(port),'--only-show-errors'],
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
            wait_port(port,transport)
            cmd=login.ssh_command(port,names[0],private,cert,known)[:-2]
            cmd+=['-N','-o','ExitOnForwardFailure=yes','-L',f'127.0.0.1:{forward}:{IP}:443',names[0]+'@127.0.0.1']
            ssh=subprocess.Popen(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
            wait_port(forward,ssh)
            route={'host':HOST,'private_destination':IP,'host_key_pinned':True,'owner_identity':login.q.OWNER,
                'transport':'Bastion plus native OpenSSH fixed local forward','vm_boundary':boundary,'private_https_responses':0}
            socket.getaddrinfo=routed_lookup(lookup,forward)
            requests.adapters.HTTPAdapter.send=response_guard(send,forward,route)
            try:
                if protected(login.q.ROOT/'host-qualified.json')!=host or protected(login.q.ROOT/'login-qualified.json')!=accepted:
                    raise ValueError('Private readback of pinned SSH evidence differs')
                yield route
            finally:
                socket.getaddrinfo=lookup;requests.adapters.HTTPAdapter.send=send;stop(ssh);stop(transport)
    finally:
        socket.getaddrinfo=lookup;requests.adapters.HTTPAdapter.send=send;stop(ssh);stop(transport)


def main():
    from run_specialist_gateway_read_verification import retain
    from rehearse_ms_worker_image_rollback import restore_protected
    from control_upgrade_readers_20261010 import save
    from datetime import datetime,timezone
    out=ROOT/'.work/platform-upgrade-20261010-stage28/archive-transport-v1'
    if out.exists():raise ValueError('Preserve previous private transport trial')
    record={'kind':'private-archive-transport-synthetic-proof','checked_at':datetime.now(timezone.utc).isoformat(),'portfolio_data':False,'credentials':False}
    with archive_tunnel() as route:
        receipt=retain(record,'private-archive-transport-20261010-v1');restored,_=restore_protected(receipt)
        if restored!=record or route['private_https_responses']<4:raise ValueError('Private exact-version roundtrip differs')
        save(out,'result',{'passed':True,'route':dict(route),'roundtrip_receipt':receipt,'new_roles':0,'secret_values_read':0,
            'remote_credentials_copied':False,'saved_dns_changed':False,'public_access_changed':False,
            'scope':'Synthetic protected append and exact-version read over fixed private endpoint; owner credentials stay local'})
    print('Private protected archive append/read passed; temporary tunnel and credentials removed')


if __name__=='__main__':main()
