"""Preserve failed scanner evidence; reconstruct exact image from verified blobs."""
import hashlib,json,os,subprocess,sys
from pathlib import Path
from datetime import datetime,timezone
import scan_ms_presentation_worker_candidate as original
from ibkr_etl.container_scan import assess_container_scan
from run_specialist_gateway_read_verification import retain
from scan_extended_crisis_identity_20261009 import exact_arm_jobs_unchanged
original.jobs_unchanged=exact_arm_jobs_unchanged

def main():
    candidate=Path(sys.argv[1]).resolve()
    source,native,output=original.verify(candidate)
    folder=candidate/'scan-v2';prior=candidate/'scan-v1'
    if folder.exists():raise ValueError('Preserve retry evidence; inspect existing attempt')
    folder.mkdir()
    digest=output['digest'];blobs={}
    def verified(digest):
        roots=(prior,original.build.ROOT/'.work/alpha-ms-distribution-v14r13-20261007/scan-v1',
            original.build.ROOT/'.work/alpha-ms-presentation-worker-candidate-20261007-r2/scan-v1',
            original.scan.ROOT/'image-scan-web-v1',original.scan.ROOT/'image-scan-web-v2')
        path=next((root/digest.split(':')[1] for root in roots if (root/digest.split(':')[1]).is_file()),None)
        if path is None:raise ValueError('Verified registry blob absent from bounded caches')
        if original.build.sha(path)!=digest.split(':')[1]:raise ValueError('Registry blob hash differs')
        blobs[digest]=path
        return path
    manifest=json.loads(verified(digest).read_bytes())
    for item in [manifest['config']]+manifest['layers']:
        if verified(item['digest']).stat().st_size!=item['size']:raise ValueError('Registry blob size differs')
    qualification=json.loads((original.scan.SCANNER/'qualification.json').read_bytes())
    executable=original.scan.SCANNER/'trivy.exe'
    if original.build.sha(executable)!=qualification['executable_sha256']:raise ValueError('Qualified scanner differs')
    image=output['registry']+'/'+output['repository']+'@'+digest
    archive=original.scan.docker_archive(blobs,manifest,folder,image)
    env={k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','PATH','TEMP','TMP','USERPROFILE','APPDATA','LOCALAPPDATA'}}
    for kind,name in [('json','vulnerabilities.json'),('cyclonedx','sbom.cdx.json')]:
        print(json.dumps({'stage':'offline_scan_retry','format':kind}),flush=True)
        run=subprocess.run([str(executable.resolve()),'image','--input',str(archive.resolve()),'--platform','linux/amd64',
            '--scanners','vuln','--format',kind,'--output',str((folder/name).resolve()),'--cache-dir',
            str((original.scan.ROOT/'scanner-cache').resolve()),'--db-repository','ghcr.io/aquasecurity/trivy-db:2','--timeout','15m','--quiet'],
            env=env,capture_output=True,text=True,timeout=1000)
        with (folder/(kind+'-scanner.log')).open('x',encoding='utf-8') as f:f.write(run.stdout+run.stderr)
        if run.returncode:raise ValueError('Retry scanner failed; gate remains closed')
    report=(folder/'vulnerabilities.json').read_bytes();sbom=(folder/'sbom.cdx.json').read_bytes()
    assessment=assess_container_scan(report_bytes=report,sbom_bytes=sbom,image_digest=digest,config_digest=manifest['config']['digest'],scanner_version=qualification['version'],now=datetime.now(timezone.utc))
    original.verify(candidate);original.jobs_unchanged(candidate)
    evidence=[(n,(candidate/(n+'.json')).read_bytes()) for n in ('source-intent','local-qualification','build-intent','build-ack','build-completed','native-build-qualified')]
    evidence += [('vulnerabilities',report),('sbom',sbom)]
    receipts={n:retain({'image':image,'name':n,'sha256':hashlib.sha256(data).hexdigest(),'content':json.loads(data)},candidate.name+'-retry-'+n) for n,data in evidence}
    record={'image':image,'native_build_run':native['run'],'assessment':assessment,'protected_artifact_receipts':receipts,
        'source_context_sha256':source['files_sha256'],'blob_digests_verified':len(blobs),'scanner_executable_sha256':qualification['executable_sha256'],
        'no_azure_credentials_given_to_scanner':True,'persistent_jobs_unchanged':True,'production_promotion':False,'source_data_changed':False,
        'retry_reason':'Completed offline scans were not sealed because the postflight attempted to recreate its preflight receipt; preserve v1 and re-scan verified bytes with idempotent protected preflight','scan_folder':'scan-v2',
        'failed_attempt_log_sha256':original.build.sha(prior/'json-scanner.log')}
    original.build.save(candidate,'scan-completed',record)
    original.build.save(candidate,'scan-protected-receipt',retain(record,candidate.name+'-retry-completed'))
    print(json.dumps({'image':image,'assessment':assessment}),flush=True)

if __name__=='__main__':main()
