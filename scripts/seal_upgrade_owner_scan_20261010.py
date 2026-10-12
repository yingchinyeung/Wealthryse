"""Reconcile detected Node manifests as well as Debian/Python; suppress nothing."""
import hashlib, json, os, subprocess, tarfile
from datetime import datetime, timezone
from pathlib import Path
from scan_ms_presentation_worker_candidate import verify, scan
from ibkr_etl.container_scan import assess_container_scan
from run_specialist_gateway_read_verification import retain
from scan_upgrade_owner_candidate_20261010 import unchanged

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.work/platform-upgrade-20261010-stage17/owner-runtime-v2'


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def streamsha(stream):
    h = hashlib.sha256()
    while part := stream.read(1024 * 1024): h.update(part)
    return h.hexdigest()


def main():
    if (OUT / 'scan-completed.json').exists(): raise ValueError('Preserve existing scan')
    source, build, output = verify(OUT); unchanged(OUT)
    folder = OUT / 'scan-v1'; digest = output['digest']; manifest_path = folder / digest.split(':')[1]
    if sha(manifest_path) != digest.split(':')[1]: raise ValueError('Manifest drift')
    manifest = json.loads(manifest_path.read_bytes()); config_digest = manifest['config']['digest']; archive = folder / 'image.docker.tar'
    with tarfile.open(archive, 'r:') as tar:
        raw = tar.extractfile(config_digest.split(':')[1] + '.json').read()
        if hashlib.sha256(raw).hexdigest() != config_digest.split(':')[1]: raise ValueError('Config drift')
        config = json.loads(raw); declared = json.load(tar.extractfile('manifest.json'))
        if len(declared) != 1 or len(declared[0]['Layers']) != len(config['rootfs']['diff_ids']): raise ValueError('Layer count drift')
        for name, expected in zip(declared[0]['Layers'], config['rootfs']['diff_ids']):
            if 'sha256:' + streamsha(tar.extractfile(name)) != expected: raise ValueError('Archive layer drift')
    qualification = json.loads((scan.SCANNER / 'qualification.json').read_bytes()); exe = scan.SCANNER / 'trivy.exe'
    if sha(exe) != qualification['executable_sha256']: raise ValueError('Scanner drift')
    env = {k: v for k, v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','PATH','TEMP','TMP','USERPROFILE','APPDATA','LOCALAPPDATA'}}
    outputs = {}
    for fmt, name in (('json', 'vulnerabilities'), ('cyclonedx', 'sbom')):
        path = folder / ('full-coverage-' + name + '.json')
        if path.exists(): raise ValueError('Preserve reconciled outputs')
        run = subprocess.run([str(exe.resolve()), 'image', '--input', str(archive.resolve()), '--platform', 'linux/amd64', '--scanners', 'vuln', '--format', fmt, '--output', str(path.resolve()), '--cache-dir', str((scan.ROOT / 'scanner-cache').resolve()), '--timeout', '15m', '--quiet'], env=env, capture_output=True, text=True, timeout=1000)
        with (folder / ('full-coverage-' + name + '.log')).open('x', encoding='utf-8') as f: f.write(run.stdout + run.stderr)
        if run.returncode: raise ValueError('Full-coverage scanner failed')
        outputs[name] = path.read_bytes()
    a = assess_container_scan(report_bytes=outputs['vulnerabilities'], sbom_bytes=outputs['sbom'], image_digest=digest,
        config_digest=config_digest, scanner_version=qualification['version'], now=datetime.now(timezone.utc),
        required_types=frozenset(('debian', 'node-pkg', 'python-pkg')))
    image = output['registry'] + '/' + output['repository'] + '@' + digest; receipts = {}
    for name in ('source-intent', 'local-qualification', 'build-intent', 'build-ack', 'build-completed', 'native-build-qualified'):
        outputs[name] = (OUT / (name + '.json')).read_bytes()
    for name, raw in outputs.items(): receipts[name] = retain({'image': image, 'name': name, 'sha256': hashlib.sha256(raw).hexdigest(), 'content': json.loads(raw)}, 'upgrade-owner-full-scan-' + name)
    unchanged(OUT); verify(OUT)
    record = {'image': image, 'assessment': a, 'native_build_run': build['run'], 'protected_artifact_receipts': receipts,
        'archive_sha256': sha(archive), 'archive_config_and_all_layers_reverified': True,
        'coverage_explanation': 'Preserved /app includes Node package metadata. Debian, Node and Python are all required and all detected findings assessed. Earlier narrow-ecosystem assertion failed and remains retained.',
        'production_promotion': False, 'persistent_jobs_unchanged': True, 'full_upgrade_complete': False}
    receipt = retain(record, 'upgrade-owner-full-scan-completed')
    for name, value in (('scan-completed', record), ('scan-protected-receipt', receipt)):
        with (OUT / (name + '.json')).open('x', encoding='utf-8') as f: json.dump(value, f, indent=2)
    print(json.dumps({'image': image, 'assessment': a}))


if __name__ == '__main__': main()
