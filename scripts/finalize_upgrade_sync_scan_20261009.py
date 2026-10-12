"""Assess retained sync scan with its observed Debian, Python and Node inventory."""
import json,hashlib,sys
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from ibkr_etl.container_scan import assess_container_scan
from deploy_viewer_private_admin_20261008 import request,rid
from run_specialist_gateway_read_verification import retain
FOLDER=ROOT/'.work/platform-upgrade-20261009-stage11/security-families-v1/image-scan-sync-v1'
DIGEST='39f73f592d98432b1c1912f57008c2171a591b3c851468ebd20fd97e4a8bdb81';IMAGE='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-etl@sha256:'+DIGEST
def main():
    if (FOLDER/'verification.json').exists():raise ValueError('Preserve existing assessment')
    manifest_bytes=(FOLDER/DIGEST).read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest()!=DIGEST:raise ValueError('Manifest digest mismatch')
    manifest=json.loads(manifest_bytes)
    for b in [manifest['config'],*manifest['layers']]:
        p=FOLDER/b['digest'].split(':')[1]
        if p.stat().st_size!=b['size'] or hashlib.sha256(p.read_bytes()).hexdigest()!=b['digest'].split(':')[1]:raise ValueError('Registry bytes drifted')
    if request('GET',rid('Microsoft.App/jobs','ibkrv2stg-account-sync-daily')+'?api-version=2024-03-01')['properties']['template']['containers'][0]['image']!=IMAGE:raise ValueError('Installed sync image changed')
    raw=(FOLDER/'vulnerabilities.json').read_bytes();sbom=(FOLDER/'sbom.cdx.json').read_bytes()
    assessment=assess_container_scan(report_bytes=raw,sbom_bytes=sbom,image_digest='sha256:'+DIGEST,config_digest=manifest['config']['digest'],scanner_version='0.75.0',now=datetime.now(timezone.utc),required_types=frozenset(('debian','python-pkg','node-pkg')))
    report=json.loads(raw);high=[{k:v.get(k) for k in ('VulnerabilityID','PkgName','InstalledVersion','FixedVersion','Severity','PrimaryURL')} for t in report['Results'] for v in t.get('Vulnerabilities',[]) if v['Severity'] in ('HIGH','CRITICAL')]
    receipts={n:retain({'image':IMAGE,'sha256':hashlib.sha256(b).hexdigest(),'content':json.loads(b)},'upgrade-sync-scan-'+n) for n,b in (('vulnerabilities',raw),('sbom',sbom))}
    result={'image':IMAGE,'assessment':assessment,'high_critical_findings':high,'high_critical_with_vendor_fix':sum(bool(v.get('FixedVersion')) for v in high),'raw_artifact_receipts':receipts,'database_writes':0,'broker_calls':0,'runtime_changed':False,'checked_at':datetime.now(timezone.utc).isoformat(),'scope':'Read-only scan of installed sync image; original Python-only coverage assumption rejected. Full observed Debian/Python/Node coverage assessed without suppressing findings. Remediation not deployed.'}
    with (FOLDER/'verification.json').open('x',encoding='utf-8') as f:json.dump({'record':result,'protected_receipt':retain(result,'upgrade-sync-scan-assessment')},f,indent=2)
    print(json.dumps({'status':assessment['status'],'counts':assessment['counts'],'high_critical_with_vendor_fix':result['high_critical_with_vendor_fix'],'runtime_changed':False}))
if __name__=='__main__':main()
