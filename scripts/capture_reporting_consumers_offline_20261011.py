"""Hash-verified, source-only inspection of six already-scanned consumer images.

Never executes an image, extracts arbitrary tar paths, reads secrets, or connects
to Azure. OCI whiteouts are applied before each layer's new regular files.
"""
import hashlib
import json
import sys
import tarfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / '.work/platform-upgrade-20261011-stage34'
TARGETS = {
    'web': ('ibkrv2stg-web', 'platform-upgrade-20261010-stage30/cash-summary-v1'),
    'snapshot': ('ibkrv2stg-report-snapshot', 'platform-upgrade-20261010-stage23/snapshot-security-v1'),
    'historical': ('ibkrv2stg-historical-reports', 'platform-upgrade-20261010-stage25/historical-security-v1'),
    'assurance': ('ibkrv2stg-analyst-assurance', 'platform-upgrade-20261009-stage11/assurance-period-release-v1'),
    'legacy-presentation': ('ibkrv2stg-presentation-v23-hist', 'platform-upgrade-20261010-stage25/legacypresentation-security-v1'),
    'onboard-render': ('ibkrv2stg-onboard-render', 'alpha-t-axis-contract-20261009-v3/worker'),
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def member_name(name):
    while name.startswith('./'):
        name = name[2:]
    name = name.lstrip('/')
    if '\\' in name or '..' in PurePosixPath(name).parts:
        raise ValueError('Unsafe archive member')
    return name.rstrip('/')


def apply_layer(sources, new, deletions, opaque):
    result = dict(sources)
    for name in list(result):
        if any(name == p or name.startswith(p + '/') for p in deletions):
            del result[name]
        elif any(not p or name.startswith(p + '/') for p in opaque):
            del result[name]
    result.update(new)
    return result


def capture(kind):
    job, location = TARGETS[kind]
    out = STAGE / ('consumer-source-' + kind + '-v1')
    if out.exists():
        raise ValueError('Preserve previous capture')
    scan = ROOT / '.work' / location / 'scan-v1'
    receipt = json.loads((scan.parent / 'scan-completed.json').read_bytes())
    mapping = json.loads((STAGE / 'consumer-map-v1/result.json').read_bytes())
    row = next(r for r in mapping['workloads'] if r['name'] == job)
    if row['images'] != [receipt['image']]:
        raise ValueError('Inventory and scanned image differ')
    digest = receipt['image'].split('@sha256:')[1]
    raw = (scan / digest).read_bytes()
    if sha(raw) != digest:
        raise ValueError('Immutable OCI manifest differs')
    manifest = json.loads(raw)
    config_digest = manifest['config']['digest'].split(':')[1]
    patch = json.loads((STAGE / 'reporting-image-v4/context/patch-manifest.json').read_bytes())
    names = set(patch['baseline']) | set(patch['candidate']) | set(row['entrypoints'])
    names.add('src/ibkr_etl/web.py')
    wanted = {'app/' + name for name in names}
    sources = {}
    layers = []
    with tarfile.open(scan / 'image.docker.tar', 'r:') as outer:
        docker = json.load(outer.extractfile('manifest.json'))
        if len(docker) != 1:
            raise ValueError('Single-platform archive required')
        docker = docker[0]
        config_raw = outer.extractfile(docker['Config']).read()
        if sha(config_raw) != config_digest:
            raise ValueError('OCI config differs')
        config = json.loads(config_raw)
        diffs = config['rootfs']['diff_ids']
        if len(docker['Layers']) != len(diffs) or len(diffs) != len(manifest['layers']):
            raise ValueError('Layer count differs')
        for layer_name, diff in zip(docker['Layers'], diffs):
            h = hashlib.sha256()
            with outer.extractfile(layer_name) as stream:
                while chunk := stream.read(1024 * 1024):
                    h.update(chunk)
            if 'sha256:' + h.hexdigest() != diff:
                raise ValueError('Uncompressed layer integrity failed')
            new, deletions, opaque = {}, set(), set()
            with tarfile.open(fileobj=outer.extractfile(layer_name), mode='r|') as layer:
                for entry in layer:
                    name = member_name(entry.name)
                    parent, _, leaf = name.rpartition('/')
                    if leaf == '.wh..wh..opq':
                        opaque.add(parent)
                    elif leaf.startswith('.wh.'):
                        deletions.add((parent + '/' if parent else '') + leaf[4:])
                    elif name in wanted:
                        if not entry.isfile() or not 0 < entry.size < 2 * 1024 * 1024:
                            raise ValueError('Expected bounded regular source file')
                        new[name] = layer.extractfile(entry).read()
                    elif (entry.issym() or entry.islnk()) and any(p.startswith(name + '/') for p in wanted):
                        raise ValueError('Source parent link requires separate review')
            sources = apply_layer(sources, new, deletions, opaque)
            layers.append(diff)
    if not sources:
        raise ValueError('No scoped application sources')
    out.mkdir()
    hashes = {}
    for name, data in sources.items():
        relative = name.removeprefix('app/')
        dest = out / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open('xb') as stream:
            stream.write(data)
        hashes[relative] = sha(data)
    differences = {n: {'installed': hashes.get(n), 'reporter_baseline': digest}
                   for n, digest in patch['baseline'].items() if hashes.get(n) != digest}
    value = {'passed': True, 'consumer': job, 'image': receipt['image'],
             'manifest_sha256': sha(raw), 'config_sha256': config_digest,
             'verified_uncompressed_layers': layers, 'files_sha256': hashes,
             'requested_but_absent': sorted(names - set(hashes)),
             'reporter_baseline_differences': differences,
             'already_candidate': sorted(n for n, h in patch['candidate'].items() if hashes.get(n) == h),
             'executed': False, 'network_requests': 0, 'live_changes': False}
    with (out / 'result.json').open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
    print(json.dumps({'consumer': job, 'files': len(hashes), 'different_baseline_files': sorted(differences), 'offline_verified': True}), flush=True)


if __name__ == '__main__':
    capture(sys.argv[1])
