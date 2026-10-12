"""Reconcile legacy Debian/Python scan scope, preserving original evidence."""
from datetime import datetime, timezone
import hashlib
import json
import os
import subprocess
import sys
import tarfile
from release_upgrade_legacy_security_20261010 import OUT, r
from scan_upgrade_legacy_candidate_20261010 import unchanged
from scan_ms_presentation_worker_candidate import verify, scan
from ibkr_etl.container_scan import assess_container_scan
from run_specialist_gateway_read_verification import retain


def digest_stream(stream):
    result = hashlib.sha256()
    while data := stream.read(1024 * 1024): result.update(data)
    return result.hexdigest()


def main():
    source, native, output = verify(OUT); unchanged(OUT)
    if (OUT / 'scan-completed.json').exists(): raise ValueError('Already sealed; do not overwrite')
    folder = OUT / 'scan-v1'; digest = output['digest']
    manifest_path = folder / digest.split(':')[1]
    if r.sha(manifest_path) != digest.split(':')[1]: raise ValueError('Manifest changed')
    manifest = json.loads(manifest_path.read_bytes())
    config_digest = manifest['config']['digest']
    archive = folder / 'image.docker.tar'
    with tarfile.open(archive, 'r:') as tar:
        config_bytes = tar.extractfile(config_digest.split(':')[1] + '.json').read()
        if hashlib.sha256(config_bytes).hexdigest() != config_digest.split(':')[1]: raise ValueError('Archive configuration drift')
        config = json.loads(config_bytes)
        declared = json.load(tar.extractfile('manifest.json'))
        if len(declared) != 1 or len(declared[0]['Layers']) != len(config['rootfs']['diff_ids']):
            raise ValueError('Archive layer count drift')
        for name, wanted in zip(declared[0]['Layers'], config['rootfs']['diff_ids']):
            if 'sha256:' + digest_stream(tar.extractfile(name)) != wanted: raise ValueError('Archive layer drift')
    qualification = json.loads((scan.SCANNER / 'qualification.json').read_bytes())
    executable = scan.SCANNER / 'trivy.exe'
    if r.sha(executable) != qualification['executable_sha256']: raise ValueError('Scanner drift')
    # Original completed outputs are kept intact. An explicit second local
    # scan of the verified archive establishes a fresh successful exit status.
    env = {k: v for k, v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','PATH','TEMP','TMP','USERPROFILE','APPDATA','LOCALAPPDATA'}}
    outputs = {}
    for kind, name in (('json', 'vulnerabilities'), ('cyclonedx', 'sbom')):
        path = folder / ('reconciled-' + name + '.json')
        if path.exists(): raise ValueError('Preserve reconciled scan; inspect before resuming')
        result = subprocess.run([str(executable.resolve()), 'image', '--input', str(archive.resolve()),
            '--platform', 'linux/amd64', '--scanners', 'vuln', '--format', kind, '--output', str(path.resolve()),
            '--cache-dir', str((scan.ROOT / 'scanner-cache').resolve()), '--timeout', '15m', '--quiet'],
            env=env, capture_output=True, text=True, timeout=1000)
        with (folder / ('reconciled-' + name + '.log')).open('x', encoding='utf-8') as f: f.write(result.stdout + result.stderr)
        if result.returncode: raise ValueError('Reconciled scanner failed')
        outputs[name] = path.read_bytes()
    assessment = assess_container_scan(report_bytes=outputs['vulnerabilities'], sbom_bytes=outputs['sbom'],
         image_digest=digest, config_digest=config_digest, scanner_version=qualification['version'], now=datetime.now(timezone.utc),
         required_types=frozenset(('debian','python-pkg')))
    for p in (OUT / 'context').rglob('*.py'): compile(p.read_bytes(), str(p), 'exec')
    image = output['registry'] + '/' + output['repository'] + '@' + digest
    artifacts = {}
    for name in ('source-intent', 'local-qualification', 'build-intent', 'build-ack', 'build-completed', 'native-build-qualified'):
        outputs[name] = (OUT / (name + '.json')).read_bytes()
    for name, data in outputs.items():
        artifacts[name] = retain({'image': image, 'name': name, 'sha256': hashlib.sha256(data).hexdigest(),
             'content': json.loads(data)}, OUT.name + '-reconciled-' + name)
    verify(OUT); unchanged(OUT)
    record = {'image': image, 'native_build_run': native['run'], 'assessment': assessment,
        'protected_artifact_receipts': artifacts, 'source_context_sha256': source['files_sha256'],
        'archive_sha256': r.sha(archive), 'archive_config_and_all_layers_reverified': True,
        'scanner_executable_sha256': qualification['executable_sha256'],
        'reconciled_after_generic_node_coverage_mismatch': True,
        'scope_explanation': 'The legacy runtime contains Debian and Python, not Node. All detected findings remain assessed; no severity or package finding was excluded.',
        'no_azure_credentials_given_to_scanner': True, 'persistent_jobs_unchanged': True,
        'production_promotion': False, 'source_data_changed': False}
    r.save(OUT / 'scan-completed.json', record)
    r.save(OUT / 'scan-protected-receipt.json', retain(record, OUT.name + '-scan-completed'))
    print(json.dumps({'image': image, 'assessment': assessment}))


if __name__ == '__main__': main()
