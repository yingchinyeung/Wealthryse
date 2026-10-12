"""Run only inside a private Linux mount namespace on the approved build VM.

No application starts, no database credentials, no host DNS/daemon changes.
Two disposable Docker daemons prove push and empty-cache pull over private IPs.
"""
import base64,hashlib,json,os,re,shutil,socket,ssl,subprocess,tempfile,time,urllib.error,urllib.parse,urllib.request
from pathlib import Path

REG='ibkrv2stgauhecwc4itaoo.azurecr.io'
DATA='ibkrv2stgauhecwc4itaoo.eastasia.data.azurecr.io'
BASE=REG+'/ibkr-platform@sha256:bd669adb8265ae75d11644cd98a1dec25fd9ba615281a2abc1536e4625d49429'
TARGET=REG+'/private-path-qualification:upgrade-20261009-v1'
PREFIX='PRIVATE_BUILD_QUALIFICATION '
HOSTS={REG:'10.50.10.5',DATA:'10.50.10.4'}
AUTH_REGISTRIES=(REG,)

def run(args,operation,*,timeout=900,input=None,env=None):
    r=subprocess.run(args,input=input,capture_output=True,text=True,timeout=timeout,env=env)
    if r.returncode:raise RuntimeError(operation+' failed with exit '+str(r.returncode))
    return r.stdout

def request(url,data=None,headers=None):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    body=None if data is None else urllib.parse.urlencode(data).encode()
    with opener.open(urllib.request.Request(url,data=body,headers=headers or {}),timeout=30) as r:
        return json.load(r)

def main():
    os.umask(0o077)
    if os.geteuid()!=0 or os.readlink('/proc/self/ns/mnt')==os.readlink('/proc/1/ns/mnt'):raise ValueError('A separate root mount namespace is mandatory')
    for tool in ('dockerd','docker','mount','umount'):
        if not shutil.which(tool):raise ValueError('Required build tool missing: '+tool)
    if shutil.disk_usage('/tmp').free<12*1024**3:raise ValueError('Insufficient disposable build space')
    root=Path(tempfile.mkdtemp(prefix='wealthryse-private-build-')).resolve()
    original=Path('/etc/hosts').read_bytes();before=hashlib.sha256(original).hexdigest()
    if any(host in original.decode() for host in HOSTS):raise ValueError('Preserve existing registry resolution override')
    hosts=root/'hosts';hosts.write_bytes(original+b'\n'+''.join(ip+' '+host+'\n' for host,ip in HOSTS.items()).encode())
    daemons=[];logs=[];mounted=False
    env={k:v for k,v in os.environ.items() if k.upper() not in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','DOCKER_HOST','DOCKER_CONTEXT','DOCKER_CONFIG','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH')}
    env['DOCKER_CONFIG']=str(root/'credentials');env['DOCKER_BUILDKIT']='0'
    Path(env['DOCKER_CONFIG']).mkdir(mode=0o700)
    results={'private_targets':[],'base_image':BASE,'qualification_tag':TARGET,'live_database_writes':0,'production_dns_changed':False,'normal_docker_daemon_changed':False}
    try:
        run(['mount','--bind',str(hosts),'/etc/hosts'],'namespace-only hosts bind',timeout=15);mounted=True
        for host,expected in HOSTS.items():
            addresses={r[4][0] for r in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)}
            if addresses!={expected}:raise ValueError('Private-only resolution failed')
            with socket.create_connection((host,443),timeout=15) as sock:
                with ssl.create_default_context().wrap_socket(sock,server_hostname=host) as secure:
                    if secure.getpeername()[0]!=expected:raise ValueError('Private TLS peer differs')
                    results['private_targets'].append({'host':host,'peer':expected,'tls':secure.version()})
        token=request('http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https%3A%2F%2Fmanagement.azure.com%2F',headers={'Metadata':'true'})['access_token']
        payload=token.split('.')[1];tenant=json.loads(base64.urlsafe_b64decode(payload+'='*(-len(payload)%4)))['tid']
        if AUTH_REGISTRIES[-1]!=REG:raise ValueError('Target authentication order differs')
        for auth_registry in AUTH_REGISTRIES:
            refresh=request('https://'+auth_registry+'/oauth2/exchange',{'grant_type':'access_token','service':auth_registry,'tenant':tenant,'access_token':token})['refresh_token']
            run(['docker','login',auth_registry,'--username','00000000-0000-0000-0000-000000000000','--password-stdin'],'managed identity registry authentication',input=refresh,timeout=60,env=env)
        del token
        access=request('https://'+REG+'/oauth2/token',{'grant_type':'refresh_token','service':REG,'scope':'repository:private-path-qualification:pull','refresh_token':refresh})['access_token']
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(urllib.request.Request('https://'+REG+'/v2/private-path-qualification/manifests/upgrade-20261009-v1',method='HEAD',headers={'Authorization':'Bearer '+access}),timeout=30):
                raise ValueError('Preserve existing qualification tag; do not overwrite')
        except urllib.error.HTTPError as e:
            if e.code!=404:raise RuntimeError('Qualification tag absence not confirmed: HTTP '+str(e.code)) from None
        del access
        del refresh
        def daemon(index):
            location=root/('daemon'+str(index));location.mkdir()
            log=(location/'daemon.log').open('w');logs.append(log)
            sock=str(location/'docker.sock')
            process=subprocess.Popen(['dockerd','--host=unix://'+sock,'--data-root='+str(location/'data'),'--exec-root='+str(location/'exec'),'--pidfile='+str(location/'pid'),'--bridge=none','--iptables=false','--ip6tables=false','--ip-forward=false','--ip-masq=false','--storage-driver=overlay2'],stdout=log,stderr=subprocess.STDOUT,env=env)
            daemons.append(process);client=['docker','--host','unix://'+sock]
            for _ in range(60):
                if process.poll() is not None:raise RuntimeError('Isolated Docker daemon exited')
                p=subprocess.run(client+['info','--format','{{.DockerRootDir}}'],capture_output=True,text=True,env=env,timeout=5)
                if p.returncode==0:
                    if Path(p.stdout.strip()).resolve()!=location/'data':raise ValueError('Docker data root differs')
                    return client,process
                time.sleep(1)
            raise RuntimeError('Isolated Docker daemon readiness timeout')
        client,first=daemon(1)
        run(client+['pull',BASE],'private base-image pull',env=env)
        print('PRIVATE_BUILD_PROGRESS '+json.dumps({'stage':'private-base-pull-passed'}),flush=True)
        context=root/'context';context.mkdir()
        (context/'Dockerfile').write_text('FROM '+BASE+'\nLABEL ai.wealthryse.purpose="private-path-qualification-only"\n')
        run(client+['build','--network=none','--pull=false','-t',TARGET,str(context)],'isolated label-only build',env=env)
        built=run(client+['image','inspect',TARGET,'--format','{{.Id}}'],'built image inspection',env=env).strip()
        run(client+['push',TARGET],'private qualification-image push',env=env)
        digests=json.loads(run(client+['image','inspect',TARGET,'--format','{{json .RepoDigests}}'],'pushed digest inspection',env=env))
        matching=[d for d in digests if d.startswith(REG+'/private-path-qualification@sha256:')]
        if len(matching)!=1 or not re.fullmatch(re.escape(REG)+r'/private-path-qualification@sha256:[a-f0-9]{64}',matching[0]):raise ValueError('Pushed digest missing')
        first.terminate();first.wait(timeout=45)
        print('PRIVATE_BUILD_PROGRESS '+json.dumps({'stage':'private-build-push-passed','image':matching[0]}),flush=True)
        client,second=daemon(2)
        if run(client+['image','ls','-q'],'empty-cache inventory',env=env).strip():raise ValueError('Cold-pull daemon is not empty')
        run(client+['pull',matching[0]],'empty-cache private digest pull',env=env)
        pulled=run(client+['image','inspect',matching[0],'--format','{{.Id}}'],'cold image inspection',env=env).strip()
        if pulled!=built:raise ValueError('Cold-pulled image differs from build')
        results.update({'passed':True,'pushed_image':matching[0],'image_config':built,'empty_cache_pull_passed':True,'application_started':False})
    finally:
        for process in reversed(daemons):
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=45)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=10)
        for log in logs:log.close()
        if mounted:run(['umount','/etc/hosts'],'namespace-only hosts unmount',timeout=15)
        if hashlib.sha256(Path('/etc/hosts').read_bytes()).hexdigest()!=before:raise ValueError('Hosts restore differs')
        # Delete only the exact newly allocated disposable root, never host Docker data.
        if root.parent!=Path('/tmp') or not root.name.startswith('wealthryse-private-build-'):raise ValueError('Disposable cleanup boundary differs')
        shutil.rmtree(root)
    print(PREFIX+json.dumps(results),flush=True)

if __name__=='__main__':main()
