"""Read-only scans of the two mutable ACR tags and the pinned Python index.

Resolve identities without changing declarations. A clean tag scan is NOT
immutable deployment qualification. Never give registry/Azure tokens to Trivy.
"""
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

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / '.work/system-improvements-20261006')]
import scan_live_image as scan
from audit_upgrade_image_coverage_20261009 import inventory
from control_upgrade_legacy_recovery_probe_20261009 import protected
from run_specialist_gateway_read_verification import retain
from ibkr_etl.container_scan import assess_container_scan
from audit_azure_runtime import az

OUT = ROOT / '.work/platform-upgrade-20261010-stage19'
ACCEPT = ','.join(('application/vnd.oci.image.manifest.v1+json',
                   'application/vnd.docker.distribution.manifest.v2+json',
                   'application/vnd.oci.image.index.v1+json',
                   'application/vnd.docker.distribution.manifest.list.v2+json'))
CHOICES = {'verify': 'ibkrv2stg-verify-u16088571', 'import': 'ibkrv2stg-import-u16088571',
           'canary': 'ibkrv2stg-ownertrial-log-canary'}


def digest(data):
    return 'sha256:' + hashlib.sha256(data).hexdigest()


def platform_manifest(value):
    if value.get('schemaVersion') != 2:
        raise ValueError('Unsupported manifest schema')
    if 'manifests' not in value:
        if 'config' not in value or not isinstance(value.get('layers'), list):
            raise ValueError('Invalid single-platform manifest')
        return None
    choices = [i for i in value['manifests'] if i.get('platform', {}).get('os') == 'linux'
               and i.get('platform', {}).get('architecture') == 'amd64'
               and i.get('platform', {}).get('variant') in (None, '')]
    if len(choices) != 1:
        raise ValueError('Exactly one Linux AMD64 manifest required')
    return choices[0]


def redirect_allowed(url, docker):
    p = urlsplit(url)
    hosts = ('production.cloudflare.docker.com', 'production.cloudfront.docker.com')
    return (p.scheme == 'https' and not p.username and not p.password and p.port in (None, 443)
            and bool(p.hostname) and
            (p.hostname in hosts if docker else p.hostname.endswith('.blob.core.windows.net')))


def main():
    p = argparse.ArgumentParser(); p.add_argument('target', choices=CHOICES); args = p.parse_args()
    intent = protected(OUT / 'intent.json')
    before = inventory()
    if before != intent['templates']:
        raise ValueError('Runtime image drift; review before scanning')
    rows = [r for r in before if r['name'] == CHOICES[args.target]]
    if len(rows) != 1 or rows[0]['image'] not in intent['deferred_references']:
        raise ValueError('Exact deferred reference required')
    declared = rows[0]['image']; docker = args.target == 'canary'
    folder = OUT / ('reference-' + args.target + '-v1'); folder.mkdir()
    if docker:
        registry, repository, ref = 'registry-1.docker.io', 'library/python', declared.split('@')[1]
        response = requests.get('https://auth.docker.io/token', params={'service': 'registry.docker.io',
            'scope': 'repository:library/python:pull'}, timeout=30, allow_redirects=False)
        if response.status_code != 200: raise ValueError('Anonymous Docker pull token unavailable')
        token = response.json()['token']
    else:
        registry = scan.REGISTRY; repository, ref = declared.split('/', 1)[1].split(':')
        credential = az('acr', 'login', '-n', registry.split('.')[0], '--expose-token')
        response = requests.post('https://' + registry + '/oauth2/token', data={'service': registry,
            'scope': 'repository:' + repository + ':pull', 'grant_type': 'refresh_token',
            'refresh_token': credential['accessToken']}, timeout=30, allow_redirects=False)
        credential = None
        if response.status_code != 200: raise ValueError('ACR pull-only token exchange failed')
        token = response.json()['access_token']
    headers = {'Authorization': 'Bearer ' + token, 'Accept': ACCEPT}; token = response = None
    def fetch(kind, reference, size=None):
        response = requests.get('https://' + registry + '/v2/' + repository + '/' + kind + '/' + reference,
            headers=headers, timeout=120, allow_redirects=False, stream=True)
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get('Location', '')
            if kind != 'blobs' or not redirect_allowed(location, docker):
                raise ValueError('Registry redirect not in reviewed allowlist')
            response.close()
            response = requests.get(location, timeout=120, allow_redirects=False, stream=True)
        if response.status_code != 200:
            raise ValueError('Registry read failed HTTP ' + str(response.status_code))
        # Bounded memory for manifests; large blobs stream directly to local disk.
        bound = 4 * 1024**2 if kind == 'manifests' else 2 * 1024**3
        fp = hashlib.sha256(); count = 0; parts = []
        path = folder / reference.split(':')[-1] if kind == 'blobs' else None
        handle = path.open('xb') if path else None
        try:
            for part in response.iter_content(1024 * 1024):
                count += len(part)
                if count > bound: raise ValueError('Registry object exceeds bound')
                fp.update(part)
                if handle: handle.write(part)
                else: parts.append(part)
        finally:
            if handle: handle.close()
            response.close()
        actual = 'sha256:' + fp.hexdigest()
        if (reference.startswith('sha256:') and reference != actual) or (size is not None and size != count):
            raise ValueError('Registry object content identity/size mismatch')
        if path: return actual, path
        data = b''.join(parts)
        path = folder / actual.split(':')[1]
        if not path.exists():
            with path.open('xb') as handle: handle.write(data)
        elif path.read_bytes() != data: raise ValueError('Immutable manifest changed')
        return actual, path
    resolved, parent = fetch('manifests', ref)
    value = json.loads(parent.read_bytes()); child = platform_manifest(value)
    image_digest, manifest_path = (fetch('manifests', child['digest'], child['size']) if child else (resolved, parent))
    manifest = json.loads(manifest_path.read_bytes())
    if platform_manifest(manifest) is not None: raise ValueError('Nested image index unsupported')
    blobs = {}
    for item in [manifest['config']] + manifest['layers']:
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', item['digest']): raise ValueError('Invalid content digest')
        if item['digest'] not in blobs:
            _, blobs[item['digest']] = fetch('blobs', item['digest'], item['size'])
    image = registry + '/' + repository + '@' + image_digest
    archive = scan.docker_archive(blobs, manifest, folder, image)
    qualification = json.loads((scan.SCANNER / 'qualification.json').read_bytes())
    executable = (scan.SCANNER / 'trivy.exe').resolve()
    if hashlib.sha256(executable.read_bytes()).hexdigest() != qualification['executable_sha256']:
        raise ValueError('Scanner binary changed')
    env = {k: v for k, v in os.environ.items() if k.upper() in
           {'SYSTEMROOT','WINDIR','PATH','TEMP','TMP','USERPROFILE','APPDATA','LOCALAPPDATA'}}
    for fmt, name in [('json','vulnerabilities.json'), ('cyclonedx','sbom.cdx.json')]:
        result = subprocess.run([str(executable), 'image', '--input', str(archive.resolve()),
            '--platform', 'linux/amd64', '--scanners', 'vuln', '--format', fmt, '--output', str((folder/name).resolve()),
            '--cache-dir', str((OUT/'remaining-security-v1/scanner-cache').resolve()), '--timeout', '15m', '--quiet'],
            env=env, capture_output=True, text=True, timeout=1000)
        with (folder/(fmt+'-scanner.log')).open('x', encoding='utf-8') as handle: handle.write(result.stdout+result.stderr)
        if result.returncode: raise ValueError('Scanner failed; retained diagnostic is not a pass')
    assessment = assess_container_scan(report_bytes=(folder/'vulnerabilities.json').read_bytes(),
        sbom_bytes=(folder/'sbom.cdx.json').read_bytes(), image_digest=image_digest,
        config_digest=manifest['config']['digest'], scanner_version=qualification['version'], now=datetime.now(timezone.utc),
        required_types=frozenset(('debian','python-pkg')) if docker else frozenset(('debian','python-pkg','node-pkg')))
    resolved_after, _ = fetch('manifests', ref)
    headers.clear()
    if resolved_after != resolved or inventory() != before:
        raise ValueError('Reference or runtime changed while scanning')
    raw = {n: retain({'name': n, 'image': image, 'content': json.loads((folder/n).read_bytes()),
                      'sha256': hashlib.sha256((folder/n).read_bytes()).hexdigest()}, 'upgrade-stage19-'+args.target+'-'+n)
           for n in ('vulnerabilities.json','sbom.cdx.json')}
    record = {'checked_at': datetime.now(timezone.utc).isoformat(), 'target': CHOICES[args.target],
        'declared_reference': declared, 'resolved_parent_digest': resolved, 'image': image,
        'assessment': assessment, 'raw_receipts': raw, 'scanner': qualification,
        'mutable_reference': not docker, 'reference_unchanged_during_scan': True,
        'linux_amd64_only': True, 'runtime_changed': False, 'credentials_given_to_scanner': False,
        'deployment_authorized': False}
    with (folder/'verification.json').open('x', encoding='utf-8') as handle:
        json.dump({**record, 'protected_receipt': retain(record, 'upgrade-stage19-reference-'+args.target)}, handle, indent=2)
    print(json.dumps({k: record[k] for k in ('target','declared_reference','image','assessment','mutable_reference')}), flush=True)


if __name__ == '__main__':
    try: main()
    except requests.RequestException as exc:
        # Signed redirect URLs/credentials must not appear in exception output.
        raise SystemExit('Registry network error: ' + type(exc).__name__) from None
