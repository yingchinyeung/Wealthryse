"""Retain failed coverage gates without relabeling them as qualified scans.

Independently bind retained manifest/config/layer bytes to current references.
The OS/Python sub-assessment validates findings but does not waive the original
three-ecosystem requirement or qualify bundled native executables.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'),str(ROOT/'.work/system-improvements-20261006')]
import scan_live_image as scan
from audit_azure_runtime import az
from audit_upgrade_image_coverage_20261009 import inventory
from control_upgrade_legacy_recovery_probe_20261009 import protected
from ibkr_etl.container_scan import assess_container_scan
from run_specialist_gateway_read_verification import retain
OUT = ROOT/'.work/platform-upgrade-20261010-stage19'


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        while part:=f.read(1024*1024): h.update(part)
    return 'sha256:'+h.hexdigest()


def main():
    intent=protected(OUT/'intent.json'); before=inventory()
    if before != intent['templates']: raise ValueError('Live inventory drift')
    families=[]
    for name,(_,repo,d) in intent['targets'].items():
        families.append((name,scan.REGISTRY+'/'+repo+'@'+d,OUT/'remaining-security-v1'/('image-scan-'+name+'-v1')))
    for key,name in [('verify','ibkrv2stg-verify-u16088571'),('import','ibkrv2stg-import-u16088571')]:
        families.append((name,next(r['image'] for r in before if r['name']==name),OUT/('reference-'+key+'-v1')))
    for name,declared,folder in families:
        if (folder/'verification.json').exists(): continue
        if (folder/'unqualified-verification.json').exists(): continue
        if not all((folder/n).exists() for n in ('vulnerabilities.json','sbom.cdx.json')):
            print(json.dumps({'target':name,'status':'raw_scan_not_complete'})); continue
        mutable='@' not in declared
        resolved=az('acr','repository','show','-n',scan.REGISTRY.split('.')[0],'--image',declared.split('/',1)[1],'--query','digest') if mutable else declared.split('@')[1]
        if not re.fullmatch(r'sha256:[0-9a-f]{64}',resolved): raise ValueError('Invalid registry digest')
        path=folder/resolved.split(':')[1]
        if sha(path)!=resolved: raise ValueError('Manifest differs from current reference')
        manifest=json.loads(path.read_bytes()); config_path=folder/manifest['config']['digest'].split(':')[1]
        if sha(config_path)!=manifest['config']['digest']: raise ValueError('Config identity differs')
        config=json.loads(config_path.read_bytes())
        if config.get('os')!='linux' or config.get('architecture')!='amd64': raise ValueError('Wrong platform')
        layers=manifest['layers']; diffids=config['rootfs']['diff_ids']
        if len(layers)!=len(diffids): raise ValueError('Layer count mismatch')
        for index,(item,diffid) in enumerate(zip(layers,diffids)):
            blob=folder/item['digest'].split(':')[1]
            if blob.stat().st_size!=item['size'] or sha(blob)!=item['digest'] or sha(folder/('layer-'+str(index)+'.tar'))!=diffid:
                raise ValueError('Retained layer differs')
        qualification=json.loads((scan.SCANNER/'qualification.json').read_bytes())
        if sha(scan.SCANNER/'trivy.exe')!='sha256:'+qualification['executable_sha256']: raise ValueError('Scanner binary drift')
        kwargs=dict(report_bytes=(folder/'vulnerabilities.json').read_bytes(),sbom_bytes=(folder/'sbom.cdx.json').read_bytes(),
            image_digest=resolved,config_digest=manifest['config']['digest'],scanner_version=qualification['version'],now=datetime.now(timezone.utc))
        try: assess_container_scan(**kwargs)
        except ValueError as exc:
            if str(exc)!='Container scanner coverage differs from reviewed build policy': raise
            reason=str(exc)
        else: raise ValueError('Unexpected passing gate; do not relabel failed run')
        partial=assess_container_scan(**kwargs,required_types=frozenset(('debian','python-pkg')))
        image=declared.split('@')[0] if not mutable else declared.rsplit(':',1)[0]
        image+='@'+resolved
        raw={n:retain({'name':n,'image':image,'sha256':sha(folder/n).split(':')[1],
                      'content':json.loads((folder/n).read_bytes())},'upgrade-stage19-unqualified-'+name+'-'+n)
             for n in ('vulnerabilities.json','sbom.cdx.json')}
        if mutable and az('acr','repository','show','-n',scan.REGISTRY.split('.')[0],'--image',declared.split('/',1)[1],'--query','digest')!=resolved:
            raise ValueError('Tag moved during reconciliation')
        if inventory()!=before: raise ValueError('Inventory drift during reconciliation')
        record={'checked_at':datetime.now(timezone.utc).isoformat(),'target':name,'declared_reference':declared,'image':image,
            'mutable_reference':mutable,'current_reference_reconciled':True,'raw_receipts':raw,'scanner':qualification,
            'assessment':{'status':'unqualified','reason':reason,'image_digest':resolved,'counts':partial['counts'],
                          'blocking_findings':partial['blocking_findings'],'deployment_authorized':False},
            'os_python_subassessment':partial,'original_three_ecosystem_gate_waived':False,
            'bundled_native_binary_security_qualified':False,'runtime_changed':False}
        receipt=retain(record,'upgrade-stage19-unqualified-'+name)
        with (folder/'unqualified-verification.json').open('x',encoding='utf-8') as f:
            json.dump({**record,'protected_receipt':receipt},f,indent=2)
        print(json.dumps({'target':name,'status':'unqualified','counts':partial['counts']}),flush=True)


if __name__=='__main__':main()
