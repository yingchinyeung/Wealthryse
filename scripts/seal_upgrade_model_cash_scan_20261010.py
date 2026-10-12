"""Reconcile completed scanner output after a missing local receipt stopped archival."""
from datetime import datetime,timezone
import hashlib,json
import build_upgrade_model_cash_20261010 as b
import scan_ms_presentation_worker_candidate as s


def main():
    folder=b.OUT;source,native,image=s.verify(folder);b.unchanged()
    if (folder/'scan-completed.json').exists():raise ValueError('Preserve existing scan completion')
    local=b.h.read(folder/'local-qualification.json')
    if local.get('passed') is not True or local['tests']!=29 or local['source_files_sha256']!=source['files_sha256']:raise ValueError('Local acceptance not exact')
    scan=folder/'scan-v1';digest=image['digest'];full=image['registry']+'/'+image['repository']+'@'+digest
    manifest_path=scan/digest.split(':')[1]
    if b.h.sha(manifest_path)!=digest.split(':')[1]:raise ValueError('Registry manifest hash differs')
    manifest=json.loads(manifest_path.read_bytes());qualification=b.h.read(s.scan.SCANNER/'qualification.json')
    if b.h.sha(s.scan.SCANNER/'trivy.exe')!=qualification['executable_sha256']:raise ValueError('Scanner changed')
    report=(scan/'vulnerabilities.json').read_bytes();sbom=(scan/'sbom.cdx.json').read_bytes()
    assessment=s.assess_container_scan(report_bytes=report,sbom_bytes=sbom,image_digest=digest,config_digest=manifest['config']['digest'],scanner_version=qualification['version'],now=datetime.now(timezone.utc))
    if assessment['status']!='pass' or assessment['blocking_findings']:raise ValueError('Raw scan not passing')
    artifacts={}
    evidence=[(name,(folder/(name+'.json')).read_bytes()) for name in ('source-intent','local-qualification','build-intent','build-ack','build-completed','native-build-qualified')]
    evidence.extend([('vulnerabilities',report),('sbom',sbom)])
    for name,data in evidence:artifacts[name]=s.retain({'image':full,'name':name,'sha256':hashlib.sha256(data).hexdigest(),'content':json.loads(data)},folder.name+'-'+name)
    record={'image':full,'native_build_run':native['run'],'assessment':assessment,'protected_artifact_receipts':artifacts,'source_context_sha256':source['files_sha256'],
        'scanner_executable_sha256':qualification['executable_sha256'],'no_azure_credentials_given_to_scanner':True,'persistent_jobs_unchanged':True,'production_promotion':False,'source_data_changed':False,
        'reconciled_completed_raw_scan':True,'initial_archive_failure':'Missing local-qualification.json after both scans completed; original raw scans preserved',
        'raw_scan_sha256':{'json':hashlib.sha256(report).hexdigest(),'sbom':hashlib.sha256(sbom).hexdigest()}}
    b.h.save(folder/'scan-completed.json',record);b.h.save(folder/'scan-protected-receipt.json',s.retain(record,folder.name+'-scan-completed'))
    print(json.dumps({'image':full,'assessment':assessment,'reconciled_not_rescanned':True}))


if __name__=='__main__':main()
