"""Scan the exact qualified MS worker candidate; never promote a service."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit
import requests

import build_ms_presentation_worker_candidate as build
sys.path.insert(0, str(build.ROOT / '.work/system-improvements-20261006'))
import scan_live_image as scan
from ibkr_etl.container_scan import assess_container_scan
from run_specialist_gateway_read_verification import retain


def verify(folder):
    source = json.loads((folder / 'source-intent.json').read_bytes())
    context = folder / 'context'
    actual = {p.relative_to(context).as_posix(): build.sha(p)
              for p in context.rglob('*') if p.is_file()}
    native = json.loads((folder / 'build-completed.json').read_bytes())
    proof = json.loads((folder / 'native-build-qualified.json').read_bytes())
    if (actual != source['files_sha256'] or native['native_status'] != 'Succeeded'
            or native['source_intent_sha256'] != build.sha(folder / 'source-intent.json')
            or proof['qualified'] is not True or proof['run'] != native['run']
            or proof['native_log_sha256'] != build.sha(folder / 'native-build.log')
            or len(native['output_images']) != 1):
        raise ValueError('Qualified frozen native candidate required')
    output = native['output_images'][0]
    if (output['registry'] != scan.REGISTRY or output['repository'] != 'ibkr-platform'
            or output['tag'] != source['tag'].split(':', 1)[1]):
        raise ValueError('Native image identity differs from scoped intent')
    return source, native, output


def jobs_unchanged(folder):
    expected = json.loads((folder / 'build-intent.json').read_bytes())['before_jobs']
    for name, binding in expected.items():
        job = build.az('containerapp', 'job', 'show', '--subscription', build.SUB,
                       '-g', build.GROUP, '-n', name)
        if (job['properties']['template']['containers'][0]['image'] != binding['image']
                or build.invariant(job) != binding['settings_invariant_sha256']):
            raise ValueError('Persistent reporting worker changed: ' + name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--folder', type=Path, required=True)
    args = parser.parse_args()
    candidate = args.folder.resolve()
    source, native, output = verify(candidate)
    if build.az('account', 'show', '--query', 'id') != build.SUB:
        raise ValueError('Unexpected Azure subscription')
    jobs_unchanged(candidate)
    folder = candidate / 'scan-v1'
    folder.mkdir(exist_ok=True)
    if any(folder.iterdir()):
        raise ValueError('Existing scan evidence must not be overwritten or relabeled')
    qualification = json.loads((scan.SCANNER / 'qualification.json').read_bytes())
    executable = scan.SCANNER / 'trivy.exe'
    if build.sha(executable) != qualification['executable_sha256']:
        raise ValueError('Qualified scanner executable changed')
    digest = output['digest']
    image = scan.REGISTRY + '/ibkr-platform@' + digest
    credential = build.az('acr', 'login', '--subscription', build.SUB,
                          '-n', build.REGISTRY, '--expose-token')
    response = requests.post('https://' + scan.REGISTRY + '/oauth2/token', data={
        'service': scan.REGISTRY, 'scope': 'repository:ibkr-platform:pull',
        'grant_type': 'refresh_token', 'refresh_token': credential['accessToken']},
        timeout=30, allow_redirects=False)
    credential = None
    if response.status_code != 200:
        raise ValueError('Repository pull-only token exchange failed')
    headers = {'Authorization': 'Bearer ' + response.json()['access_token'],
               'Accept': 'application/vnd.oci.image.manifest.v1+json,application/vnd.docker.distribution.manifest.v2+json'}
    response = None
    blobs = {}
    caches = (build.ROOT / '.work/alpha-ms-distribution-v14r13-20261007/scan-v1',
              build.ROOT / '.work/alpha-ms-presentation-worker-candidate-20261007-r2/scan-v1',
              scan.ROOT / 'image-scan-web-v1', scan.ROOT / 'image-scan-web-v2')

    def get_blob(kind, wanted, size=None):
        if not isinstance(wanted, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', wanted):
            raise ValueError('Exact registry content digest required')
        if wanted in blobs:
            return blobs[wanted]
        for cache in caches:
            old = cache / wanted.split(':')[1]
            if (old.is_file() and not old.is_symlink() and old.stat().st_size <= 2 * 1024**3
                    and (size is None or old.stat().st_size == size)
                    and build.sha(old) == wanted.split(':')[1]):
                blobs[wanted] = old
                return old
        response = requests.get('https://' + scan.REGISTRY + '/v2/ibkr-platform/' + kind + '/' + wanted,
                                headers=headers, timeout=120, stream=True, allow_redirects=False)
        if response.status_code in (301, 302, 303, 307, 308):
            destination = response.headers['Location']
            parsed = urlsplit(destination)
            if (parsed.scheme != 'https' or not parsed.hostname
                    or not parsed.hostname.endswith('.blob.core.windows.net')
                    or parsed.username or parsed.password):
                raise ValueError('Untrusted registry download redirect')
            response = requests.get(destination, timeout=120, stream=True, allow_redirects=False)
        if response.status_code != 200:
            raise ValueError('Registry content read failed')
        path = folder / wanted.split(':')[1]
        fingerprint, count = hashlib.sha256(), 0
        with path.open('xb') as stream:
            for part in response.iter_content(1024 * 1024):
                count += len(part)
                if count > 2 * 1024**3:
                    raise ValueError('Registry blob exceeds bounded size')
                fingerprint.update(part)
                stream.write(part)
        if fingerprint.hexdigest() != wanted.split(':')[1] or size is not None and count != size:
            raise ValueError('Registry blob digest/size mismatch')
        blobs[wanted] = path
        return path

    manifest = json.loads(get_blob('manifests', digest).read_bytes())
    if (manifest.get('schemaVersion') != 2 or not isinstance(manifest.get('config'), dict)
            or not isinstance(manifest.get('layers'), list)):
        raise ValueError('Single-platform immutable image manifest required')
    for item in [manifest['config']] + manifest['layers']:
        get_blob('blobs', item['digest'], item['size'])
    headers.clear()
    print(json.dumps({'stage': 'registry_bytes_verified', 'image': image, 'blobs': len(blobs)}), flush=True)
    archive = scan.docker_archive(blobs, manifest, folder, image)
    environment = {k: v for k, v in os.environ.items() if k.upper() in
                   {'SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA'}}
    for kind, name in [('json', 'vulnerabilities.json'), ('cyclonedx', 'sbom.cdx.json')]:
        print(json.dumps({'stage': 'offline_scan', 'format': kind}), flush=True)
        result = subprocess.run([str(executable.resolve()), 'image', '--input', str(archive.resolve()),
            '--platform', 'linux/amd64', '--scanners', 'vuln', '--format', kind,
            '--output', str((folder / name).resolve()), '--cache-dir',
            str((scan.ROOT / 'scanner-cache').resolve()), '--timeout', '15m', '--quiet'],
            env=environment, capture_output=True, text=True, timeout=1000)
        with (folder / (kind + '-scanner.log')).open('x', encoding='utf-8') as stream:
            stream.write(result.stdout + result.stderr)
        if result.returncode:
            raise ValueError('Scanner failed; this is not a passing gate')
    report = (folder / 'vulnerabilities.json').read_bytes()
    sbom = (folder / 'sbom.cdx.json').read_bytes()
    assessment = assess_container_scan(report_bytes=report, sbom_bytes=sbom, image_digest=digest,
        config_digest=manifest['config']['digest'], scanner_version=qualification['version'],
        now=datetime.now(timezone.utc))
    verify(candidate)
    jobs_unchanged(candidate)
    # Retain exact source/build receipts and raw scanner output create-only in the
    # existing protected evidence archive. No released report or source is replaced.
    artifacts = {}
    evidence = [(name, (candidate / (name + '.json')).read_bytes()) for name in
                ('source-intent', 'local-qualification', 'build-intent', 'build-ack',
                 'build-completed', 'native-build-qualified')]
    evidence.extend([('vulnerabilities', report), ('sbom', sbom)])
    for name, data in evidence:
        artifacts[name] = retain({'image': image, 'name': name,
            'sha256': hashlib.sha256(data).hexdigest(), 'content': json.loads(data)},
            candidate.name + '-' + name)
    record = {'image': image, 'native_build_run': native['run'], 'assessment': assessment,
              'protected_artifact_receipts': artifacts, 'source_context_sha256': source['files_sha256'],
              'blob_digests_verified': len(blobs),
              'scanner_executable_sha256': qualification['executable_sha256'],
              'no_azure_credentials_given_to_scanner': True, 'persistent_jobs_unchanged': True,
              'production_promotion': False, 'source_data_changed': False}
    build.save(candidate, 'scan-completed', record)
    protected = retain(record, candidate.name + '-scan-completed')
    build.save(candidate, 'scan-protected-receipt', protected)
    print(json.dumps({'image': image, 'assessment': assessment, 'production_promotion': False}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except requests.RequestException as error:
        # Never expose signed blob URLs or authorization headers in traceback output.
        raise SystemExit('Registry network check failed: ' + type(error).__name__) from None
