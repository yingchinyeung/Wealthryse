"""Read-only classification of exact commands, without executing payloads."""
import ast,base64,gzip,hashlib,json,lzma
from pathlib import Path
from datetime import datetime,timezone
from deploy_viewer_private_admin_20261008 import request,rid
from deploy_ms_presentation_worker_release import settings_invariant
from run_specialist_gateway_read_verification import retain
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261010-stage16'
SOURCE='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:276a4783bf4ddf437bd5d449e08df39729fb43f449975082af80fd4c13e1142c'
API='?api-version=2026-07-01'


def inspect_source(source,depth=0):
    if depth>4 or len(source)>2000000:raise ValueError('Payload outside bounded scope')
    tree=ast.parse(source);imports=set();calls=set();children=[]
    for node in ast.walk(tree):
        if isinstance(node,ast.Import):imports.update(x.name for x in node.names)
        elif isinstance(node,ast.ImportFrom):imports.add(node.module or '')
        elif isinstance(node,ast.Call):
            name=ast.unparse(node.func);calls.add(name)
            if name in ('base64.b64decode','b64decode') and len(node.args)==1 and isinstance(node.args[0],ast.Constant) and isinstance(node.args[0].value,(str,bytes)):
                try:
                    raw=base64.b64decode(node.args[0].value,validate=True)
                    if raw.startswith(b'\x1f\x8b'):raw=gzip.decompress(raw)
                    elif raw.startswith(b'\xfd7zXZ\x00'):raw=lzma.decompress(raw)
                    children.append(inspect_source(raw.decode(),depth+1))
                except (ValueError,UnicodeError,SyntaxError):pass
    return {'sha256':hashlib.sha256(source.encode() if isinstance(source,str) else source).hexdigest(),
            'imports':sorted(imports),'calls':sorted(calls),'embedded':children}


def main():
    OUT.mkdir(exist_ok=True)
    path=OUT/'manual-command-inventory-v3.json'
    if path.exists():raise ValueError('Preserve existing inventory')
    prior=json.loads((ROOT/'.work/platform-upgrade-20261010-stage15/image-coverage-v1.json').read_bytes())
    jobs=next(i['templates'] for i in prior['images'] if i['image']==SOURCE)
    result=[]
    for name in jobs:
        job=request('GET',rid('Microsoft.App/jobs',name)+API)
        c=job['properties']['template']['containers'][0];args=c.get('args') or [];command=c.get('command') or []
        if c['image']!=SOURCE:raise ValueError('Image drift: '+name)
        row={'job':name,'settings_invariant':settings_invariant(job),'trigger':job['properties']['configuration']['triggerType'],
             'image':c['image'],'command':command,'args_count':len(args),
             'args_sha256':hashlib.sha256(json.dumps(args).encode()).hexdigest(),
             'env_names':sorted(e['name'] for e in c.get('env',[])),
             'identity_settings':job['properties']['configuration'].get('identitySettings',[])}
        full=command+args
        if (len(full)==3 and full[:2]==['python','-c']) or (len(full)==5 and full[:4]==['python','-I','-B','-c']):row['payload']=inspect_source(full[-1]);row['kind']='inline_python'
        elif len(full)==2 and full[0]=='python' and full[1].startswith('scripts/') and full[1].endswith('.py'):row.update(kind='python_file',script=full[1])
        else:row['kind']='requires_manual_review'
        result.append(row)
        print(json.dumps({'job':name,'kind':row['kind'],'trigger':row['trigger'],'top_level_imports':row.get('payload',{}).get('imports',[])}),flush=True)
    record={'checked_at':datetime.now(timezone.utc).isoformat(),'source_image':SOURCE,'jobs':result,
            'payloads_executed':False,'runtime_changed':False,'full_upgrade_complete':False}
    with path.open('x',encoding='utf-8') as f:json.dump({**record,'protected_receipt':retain(record,'upgrade-manual-command-inventory-v3')},f,indent=2)


if __name__=='__main__':main()
