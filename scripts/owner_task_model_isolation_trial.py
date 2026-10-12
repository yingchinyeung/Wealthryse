"""Fixed step-2 model/credential trial; no SQL, source data or owner task API.

This runs on separate compute with a separate identity. A Linux companion probes
only a synthetic parent-memory address, never actual credential bytes. Runtime
credentials are held by the hardened trusted parent, not CLI environment/argv.
No deployment or credential discovery occurs on import.
"""
import asyncio
import base64
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

OPERATION = 'eb757d68-f32e-4f94-b285-c79d9cad5b57'
JOB = 'ibkrv2stg-ownmodel-20261005'
IDENTITY_NAME = 'ibkrv2stg-ownertrial-model-id'
PREFIX = 'OWNER_TASK_MODEL_ISOLATION_TRIAL_V1 '
PATH = 'scripts/owner_task_model_isolation_trial.py'
TENANT = '0578a50b-a7a1-40a4-905b-8b20dba84124'
BASE = '/subscriptions/3956e821-cee4-403f-8708-e84e2ed490eb/resourceGroups/rg-ibkr-database-v11/providers/'
SERVER = BASE+'Microsoft.DBforPostgreSQL/flexibleServers/ibkrv2stg-ownertrial-20261004'
MI = BASE+'Microsoft.ManagedIdentity/userAssignedIdentities/'+IDENTITY_NAME
JOB_ID = BASE+'Microsoft.App/jobs/'+JOB
SECRET_URL = 'https://ibkrv2stg-auhecwc4itaoo.vault.azure.net/secrets/ownertrial-20261004-admin-url/88cb24e00839444cb528ffe84427bb20?api-version=7.4'
ARM_URL = 'https://management.azure.com'+SERVER+'?api-version=2024-08-01'
AI_URL = 'https://ibkrv2stg-ai-auhecwc4itaoo.openai.azure.com/openai/v1/responses'
AUDIENCES = frozenset(('https://vault.azure.net', 'https://management.azure.com/', 'https://ai.azure.com'))
HELPERS = {
 '/probe/src/ibkr_etl/codex_runtime_probe.py': '52028b350ccbb27a59fa974de2be0e714d7807c885c7479c10076d23010716b7',
 '/probe/src/ibkr_etl/codex_exec_probe.py': '94406eb7ee4de3c0e5a32d0428ae053d71ac50ef4009951820391c8c2d90239c',
 '/probe/scripts/run_codex_exec_probe.py': 'ebd1ac4ac22c5598e41de94c15725af7ffaa8366c75ff3158e8df92a7224c15a',
}
BINARY = '12eb3e81114588aca3b7998f4f19e8997b056aca08e57a7ca7c8a3ec8c652aad'
CODE_HOST = '37cab1584302611e9936902219640ab5e7a79fcfccd2504c6e85ea8cb97d0e10'


class IsolationError(ValueError):
 def __init__(self, code):
  self.code = code if type(code) is str and re.fullmatch('model_isolation_[a-z_]+',code) else 'model_isolation_unaccepted'
  super().__init__(self.code)


def require(value, code):
 if not value: raise IsolationError(code)


def canonical(value):
 return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()


def uuid_value(value):
 require(type(value)is str and re.fullmatch('[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}',value),'model_isolation_identity_invalid')
 return value


class NoRedirect(HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs): return None


def identity_request(environment,client,audience):
 uuid_value(client)
 require(audience in AUDIENCES and environment.get('AZURE_CLIENT_ID')==client,'model_isolation_identity_invalid')
 try:
  parsed=urlsplit(environment.get('IDENTITY_ENDPOINT','')); port=parsed.port
 except ValueError: raise IsolationError('model_isolation_identity_invalid') from None
 header=environment.get('IDENTITY_HEADER','')
 require(parsed.scheme=='http' and parsed.hostname in ('localhost','127.0.0.1') and type(port)is int and port>0
  and parsed.path=='/msi/token' and not any((parsed.query,parsed.fragment,parsed.username,parsed.password))
  and type(header)is str and 0<len(header)<=4096 and not any(c.isspace() for c in header), 'model_isolation_identity_invalid')
 query=urlencode({'api-version':'2019-08-01','resource':audience,'client_id':client})
 return Request('http://127.0.0.1:'+str(port)+'/msi/token?'+query,headers={'X-IDENTITY-HEADER':header})


def token(environment,client,principal,audience):
 uuid_value(principal)
 request=identity_request(environment,client,audience)
 with build_opener(ProxyHandler({}),NoRedirect()).open(request,timeout=15) as reply:
  require(reply.status==200,'model_isolation_identity_unavailable'); raw=reply.read(32769)
 require(len(raw)<=32768,'model_isolation_identity_invalid')
 try:
  value=json.loads(raw); bearer=value['access_token']
  require(type(bearer)is str and 0<len(bearer)<=16384 and not any(c.isspace() for c in bearer),'model_isolation_identity_invalid')
  pieces=bearer.split('.');require(len(pieces)==3,'model_isolation_identity_invalid')
  claims=json.loads(base64.urlsafe_b64decode(pieces[1]+'='*((-len(pieces[1]))%4)))
  require(claims.get('aud')==audience and claims.get('tid')==TENANT and claims.get('oid')==principal
   and claims.get('appid',claims.get('azp'))==client and type(claims.get('exp'))is int
   and claims['exp']>time.time()+90,'model_isolation_identity_invalid')
 except (KeyError,ValueError,TypeError): raise IsolationError('model_isolation_identity_invalid') from None
 # Decoding is a binding check, NOT independent JWT signature validation. The
 # exact managed endpoint and successful/denied Azure services validate tokens.
 return bearer


def denial(url,bearer,kind):
 require((kind,url) in (('secret',SECRET_URL),('server',ARM_URL)),'model_isolation_denial_target_invalid')
 try:
  with build_opener(ProxyHandler({}),NoRedirect()).open(Request(url,headers={'Authorization':'Bearer '+bearer}),timeout=20):
   # Never read/retain an unexpected successful response or secret body.
   raise IsolationError('model_isolation_credential_boundary_failed')
 except HTTPError as error:
  try:
   require(error.code==403,'model_isolation_denial_unverified'); raw=error.read(8193)
   require(len(raw)<=8192,'model_isolation_denial_unverified'); body=json.loads(raw)
   code=body.get('error',{}).get('code'); inner=body.get('error',{}).get('innererror',{}).get('code')
   require((kind=='secret' and code=='Forbidden' and inner=='ForbiddenByRbac')
    or (kind=='server' and code=='AuthorizationFailed'),'model_isolation_denial_unverified')
   return dict(target=kind,status=403,error_code=code,inner_error_code=inner,
    effective_service_denial_verified=True,unexpected_success_body_read=False)
  finally: error.close()


def harden_parent():
 require(sys.platform=='linux' and os.getuid()==os.geteuid()==10001 and os.getgid()==os.getegid()==10001,'model_isolation_linux_identity_invalid')
 import resource
 status=Path('/proc/self/status').read_text()
 require(re.search(r'^CapEff:\s+0+$',status,re.M) is not None,'model_isolation_linux_capabilities_invalid')
 libc=ctypes.CDLL(None,use_errno=True)
 libc.prctl.argtypes=[ctypes.c_int,*([ctypes.c_ulong]*4)];libc.prctl.restype=ctypes.c_int
 require(libc.prctl(4,0,0,0,0)==0 and libc.prctl(3,0,0,0,0)==0,'model_isolation_parent_protection_failed')
 require(libc.prctl(38,1,0,0,0)==0 and libc.prctl(39,0,0,0,0)==1,'model_isolation_parent_protection_failed')
 resource.setrlimit(resource.RLIMIT_CORE,(0,0))
 return dict(uid=10001,gid=10001,effective_capabilities_zero=True,dumpable=0,no_new_privileges=1,core_dump_limit=0)


COMPANION = r'''
import ctypes,errno,json,os,sys
pid=int(sys.argv[1]);address=int(sys.argv[2]);out={}
def probe(name,fn):
 try:fn();out[name]={'denied':False,'errno':None}
 except OSError as e:out[name]={'denied':e.errno in (errno.EPERM,errno.EACCES),'errno':e.errno}
def file_open(path):
 fd=os.open(path,os.O_RDONLY);os.close(fd)
probe('parent_environ',lambda:file_open('/proc/'+str(pid)+'/environ'))
probe('parent_mem',lambda:file_open('/proc/'+str(pid)+'/mem'))
probe('parent_fds',lambda:os.listdir('/proc/'+str(pid)+'/fd'))
probe('parent_root',lambda:os.readlink('/proc/'+str(pid)+'/root'))
class IOV(ctypes.Structure):_fields_=[('base',ctypes.c_void_p),('length',ctypes.c_size_t)]
libc=ctypes.CDLL(None,use_errno=True);libc.process_vm_readv.restype=ctypes.c_ssize_t
libc.process_vm_readv.argtypes=[ctypes.c_int,ctypes.POINTER(IOV),ctypes.c_ulong,ctypes.POINTER(IOV),ctypes.c_ulong,ctypes.c_ulong]
buffer=ctypes.create_string_buffer(1);local=IOV(ctypes.addressof(buffer),1);remote=IOV(address,1)
def memory():
 n=libc.process_vm_readv(pid,ctypes.byref(local),1,ctypes.byref(remote),1,0)
 if n<0:raise OSError(ctypes.get_errno(),'synthetic memory probe')
probe('parent_synthetic_memory',memory)
print(json.dumps({'uid':os.getuid(),'gid':os.getgid(),'probes':out},sort_keys=True))
'''


def validate_companion(value):
 names={'parent_environ','parent_mem','parent_fds','parent_root','parent_synthetic_memory'}
 require(type(value)is dict and set(value)=={'uid','gid','probes'} and type(value['uid'])is int and type(value['gid'])is int
  and value['uid']==value['gid']==10001 and type(value['probes'])is dict and set(value['probes'])==names,'model_isolation_companion_invalid')
 require(all(type(v)is dict and set(v)=={'denied','errno'} and v['denied']is True and type(v['errno'])is int
  and v['errno'] in (errno.EPERM,errno.EACCES) for v in value['probes'].values()),'model_isolation_companion_access_failed')
 return dict(value,same_uid_adversarial_checks_verified=True,actual_credential_bytes_probed=False)


def companion(directory):
 canary=ctypes.create_string_buffer(b'owner-model-isolation-synthetic-canary-only')
 proc=subprocess.run([sys.executable,'-I','-B','-c',COMPANION,str(os.getpid()),str(ctypes.addressof(canary))],
  env={'PATH':'/usr/local/bin:/usr/bin:/bin','LANG':'C.UTF-8'},cwd=directory,close_fds=True,
  stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=15)
 require(proc.returncode==0 and 0<len(proc.stdout)<=8192,'model_isolation_companion_invalid')
 return validate_companion(json.loads(proc.stdout))


def image():
 for path,wanted in {**HELPERS,'/usr/local/bin/codex':BINARY,'/usr/local/bin/codex-code-mode-host':CODE_HOST}.items():
  require(hashlib.sha256(Path(path).read_bytes()).hexdigest()==wanted,'model_isolation_image_changed')
 artifact=json.loads(Path('/probe/runtime-artifact.json').read_text())
 require(artifact.get('runtime_version')=='0.160.0' and artifact.get('binary_sha256')==BINARY
  and artifact.get('code_mode_host_sha256')==CODE_HOST,'model_isolation_image_changed')
 return artifact


def upstream(request):
 require(request.full_url==AI_URL and request.method=='POST','model_isolation_model_target_invalid')
 with build_opener(ProxyHandler({}),NoRedirect()).open(request,timeout=60) as response:
  require(response.status==200 and response.headers.get('Content-Type','').startswith('text/event-stream'),'model_isolation_model_response_invalid')
  raw=response.read(262145)
 require(len(raw)<=262144,'model_isolation_model_response_invalid')
 return raw


def main(client,principal):
 result=dict(status='unaccepted',operation_id=OPERATION,job_resource_id=JOB_ID,identity_resource_id=MI,
  source_rows_read=0,source_rows_written=0,provider_routines_invoked=0,sql_connections=0,
  owner_runtime_active=False,six_specialist_runtimes_active=False,production_changed=False,
  model_credential_isolation_verified=False,step_2_complete=False,automatic_retry=False)
 phase='parent_protection'
 try:
  uuid_value(client);uuid_value(principal)
  result['parent_protection']=harden_parent()
  require(not any(k in os.environ for k in ('OWNERTRIAL_ADMIN_URL','DATABASE_ADMIN_URL','DATABASE_URL','PGPASSWORD','PGPASSFILE','PGSERVICEFILE','AZURE_CLIENT_SECRET','OPENAI_API_KEY')),'model_isolation_ambient_credential_present')
  result['artifact']=image()
  sys.path.insert(0,'/probe/src');sys.path.insert(0,'/probe/scripts')
  from ibkr_etl.codex_runtime_probe import child_environment
  from ibkr_etl.codex_exec_probe import ExecSyntheticRelay,exec_probe
  from run_codex_exec_probe import CHECKS,verify_catalog
  phase='effective_secret_denial'
  bearer=token(os.environ,client,principal,'https://vault.azure.net')
  result['secret_denial']=denial(SECRET_URL,bearer,'secret');bearer=None
  phase='effective_server_denial'
  bearer=token(os.environ,client,principal,'https://management.azure.com/')
  result['server_denial']=denial(ARM_URL,bearer,'server');bearer=None
  with tempfile.TemporaryDirectory(prefix='owner-model-isolation-') as name:
   directory=Path(name)
   for part in ('home','codex-home','tmp','workspace'):(directory/part).mkdir()
   phase='same_uid_process_probes';result['process_denials']=companion(directory/'workspace')
   phase='native_catalog';result['catalog']=verify_catalog(directory,child_environment(directory,'synthetic-no-credential'))
   phase='fixed_model_turn'
   bearer=token(os.environ,client,principal,'https://ai.azure.com')
   with ExecSyntheticRelay(bearer,transport=upstream) as relay:
    try:result['model_result']=asyncio.run(exec_probe(relay,directory))
    finally:
     result['relay']=dict(request_count=relay.request_count,accepted_count=relay.accepted_count,
      response_sha256=relay.response_sha256,failure=relay.failure,upstream_tools_enabled=False)
    require(all(result['model_result'].get(k)is True for k in CHECKS) and relay.request_count==relay.accepted_count==1
     and relay.failure is None,'model_isolation_model_turn_unaccepted')
    result['restriction']=relay.restriction_evidence()
   bearer=None
   # Recheck the kernel boundary after the actual CLI process exited. No actual
   # SQL/AI bearer address is supplied to the adversarial companion.
   result['post_model_process_denials']=companion(directory/'workspace')
  result.update(status='model_isolation_trial_passed',model_credential_isolation_verified=True,
   configured_sql_credentials=0,model_calls=1,credential_access_denial_verified=True,
   accepted_scope='fixed_tool_free_turn_on_separate_compute',arbitrary_tool_execution_accepted=False)
 except Exception as error:
  result['failure_phase']=phase
  result['reason']=error.code if isinstance(error,IsolationError) else 'model_isolation_unaccepted'
 require(len(canonical(result))<=8192,'model_isolation_output_limit')
 print(PREFIX+canonical(result).decode(),flush=True)
 return result
