"""Pinned native OpenSSH through a Bastion tunnel; no unsafe CLI SSH wrapper."""
import hashlib,json,shlex,socket,subprocess,tempfile,time
from pathlib import Path
import qualify_viewer_private_admin_20261008 as q
from audit_azure_runtime import AZ,RG,az
from qualify_viewer_database_recovery_20261008 import read,save

SSH=Path('C:/Windows/System32/OpenSSH/ssh.exe')
KEYGEN=SSH.with_name('ssh-keygen.exe')

def ssh_command(port,username,private,certificate,known):
    return [str(SSH),'-p',str(port),'-i',str(private),'-o','CertificateFile='+json.dumps(certificate.as_posix()),'-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=20','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+json.dumps(known.as_posix()),'-o','GlobalKnownHostsFile=NUL','-o','HostKeyAlias='+q.VM,'-o','LogLevel=ERROR',username+'@127.0.0.1',shlex.join(['python3','-c',q.LOGIN_CODE])]

def principals(output):
    lines=output.splitlines();result=[];active=False
    for line in lines:
        text=line.strip()
        if text=='Principals:':active=True;continue
        if active:
            if text.startswith('Critical Options:') or text.startswith('Extensions:'):break
            if text:result.append(text)
    return result

def owner_certificate_matches(names,owner):
    return len(names)==1 and names[0].casefold()==owner.casefold()

def main():
    q.gate();host=read(q.ROOT,'host-qualified')
    if (q.ROOT/'login-qualified.json').exists():raise ValueError('Preserve qualified real-login evidence')
    known=q.ROOT/'known-hosts';content=q.VM+' '+host['evidence']['ssh_host_key']+'\n'
    if not known.exists() or known.read_text()!=content:raise ValueError('Use the unchanged management-plane-pinned SSH host key')
    owner=az('ad','signed-in-user','show','--query','userPrincipalName')
    with tempfile.TemporaryDirectory(prefix='pinned-entra-login-',dir=q.ROOT) as folder:
        target=Path(folder).resolve()
        if target.parent!=q.ROOT.resolve():raise ValueError('Credential temporary directory outside exact qualification root')
        cert=target/'id_rsa-aadcert.pub';private=target/'id_rsa'
        preflight=ssh_command(22,'owner',private,cert,known)
        config=subprocess.run([preflight[0],'-G',*preflight[1:]],capture_output=True,text=True,timeout=15)
        if config.returncode:raise ValueError('Native SSH local configuration syntax failed; do not request credentials/connect')
        result=subprocess.run([AZ,'-m','azure.cli','ssh','cert','--file',str(cert),'--only-show-errors'],capture_output=True,text=True,timeout=90)
        if result.returncode or not cert.exists() or not private.exists():print(json.dumps({'login':'not_qualified','phase':'owner_certificate','exit_code':result.returncode,'raw_authentication_output_retained':False}));return
        public=subprocess.run([str(KEYGEN),'-L','-f',str(cert)],capture_output=True,text=True,timeout=15)
        names=principals(public.stdout)
        if public.returncode or not owner_certificate_matches(names,owner):raise ValueError('Certificate must bind the current approved owner only')
        with socket.socket() as reserve:reserve.bind(('127.0.0.1',0));port=reserve.getsockname()[1]
        transport=subprocess.Popen([AZ,'-m','azure.cli','network','bastion','tunnel','-n',q.BASTION,'-g',RG,'--target-resource-id',q.VM_ID,'--resource-port','22','--port',str(port),'--only-show-errors'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            deadline=time.monotonic()+45;ready=False
            while time.monotonic()<deadline and transport.poll() is None:
                try:
                    with socket.create_connection(('127.0.0.1',port),timeout=1):ready=True;break
                except OSError:time.sleep(.25)
            if not ready:print(json.dumps({'login':'not_qualified','phase':'bastion_tunnel','raw_authentication_output_retained':False}));return
            result=subprocess.run(ssh_command(port,names[0],private,cert,known),capture_output=True,text=True,timeout=60)
            if result.returncode:
                error=result.stderr.lower()
                print(json.dumps({'login':'not_qualified','phase':'pinned_native_ssh','exit_code':result.returncode,'host_key_check_failed':'remote host identification has changed' in error or 'host key verification failed' in error,'authentication_denied':'permission denied' in error,'connection_closed':'connection closed' in error,'connection_timeout':'timed out' in error,'local_config_error':'extra arguments at end of line' in error or 'bad configuration option' in error,'raw_authentication_output_retained':False}));return
            evidence=q.parse_evidence(result.stdout,'login')
            expected=hashlib.sha256(b'ibkr-private-admin').hexdigest()
            if evidence['hostname_sha256']!=expected:raise ValueError('Authenticated remote hostname differs')
            record={'status':'passed','auth_type':'Entra ID','owner':q.OWNER,'host_key_pinned':True,'host_key_checking':'strict','transport':'Bastion tunnel plus native OpenSSH','evidence':evidence,'ephemeral_credentials_retained':False,'public_access_restriction_allowed':False,'controller_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        finally:
            if transport.poll() is None:
                transport.terminate()
                try:transport.wait(timeout=10)
                except subprocess.TimeoutExpired:transport.kill();transport.wait(timeout=10)
    save(q.ROOT,'login-qualified',record)
    print('Real owner Entra SSH login and passwordless administration passed through Bastion with strict pinned-host verification; temporary credential files removed')

if __name__=='__main__':main()
