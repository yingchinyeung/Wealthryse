"""Read-only exact installed ACR image scans; never starts or changes a job.

Reuse the qualified scanner/archive verifier. Each subprocess has independent
evidence; a failed or interrupted target must not overwrite an earlier attempt.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / '.work/system-improvements-20261006')]
import scan_live_image as scan
from audit_upgrade_image_coverage_20261009 import inventory
from control_upgrade_legacy_recovery_probe_20261009 import protected
from deploy_viewer_private_admin_20261008 import request
from run_specialist_gateway_read_verification import retain

OUT = ROOT / '.work/platform-upgrade-20261010-stage19'
CHECKPOINT = ROOT / '.work/platform-upgrade-20261010-stage18/image-coverage-v1.json'


def targets(checkpoint):
    result = {}
    for item in checkpoint['images']:
        image = item['image']
        if item['protected_scan_verified'] or not image.startswith(scan.REGISTRY + '/') or '@sha256:' not in image:
            continue
        repository, digest = image.split('/', 1)[1].split('@')
        name = item['templates'][0]
        result[name] = ('Microsoft.App/jobs/' + name, repository, digest)
    if len(result) != 10:
        raise ValueError('Expected ten unresolved digest-pinned ACR image families')
    return result


def prepare():
    if OUT.exists():
        raise ValueError('Preserve existing batch evidence')
    checkpoint = protected(CHECKPOINT)
    rows = inventory()
    if rows != checkpoint['templates']:
        raise ValueError('Live inventory differs from stage18; review drift first')
    if shutil.disk_usage(ROOT).free < 100 * 1024**3:
        raise ValueError('Insufficient safe batch scan space')
    wanted = targets(checkpoint)
    OUT.mkdir()
    (OUT / 'remaining-security-v1').mkdir()
    shutil.copytree(scan.ROOT / 'scanner-cache', OUT / 'remaining-security-v1/scanner-cache')
    record = {'templates': rows, 'targets': wanted, 'scope': 'Read-only pinned ACR image qualification; no executions or runtime mutations',
              'deferred_references': [i['image'] for i in checkpoint['images'] if not i['protected_scan_verified'] and
                                      not (i['image'].startswith(scan.REGISTRY + '/') and '@sha256:' in i['image'])]}
    with (OUT / 'intent.json').open('x', encoding='utf-8') as handle:
        json.dump({**record, 'protected_receipt': retain(record, 'upgrade-stage19-scan-intent')}, handle, indent=2)
    print(json.dumps({'targets': len(wanted), 'deferred': len(record['deferred_references'])}), flush=True)


def run_one(name):
    with (OUT / (name + '.log')).open('x', encoding='utf-8') as log:
        done = subprocess.run([sys.executable, str(Path(__file__).resolve()), 'target', name],
                              cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=3600)
    return {'target': name, 'exit_code': done.returncode}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('prepare', 'batch', 'target', 'seal'))
    parser.add_argument('target', nargs='?')
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare()
        return
    intent = protected(OUT / 'intent.json')
    if inventory() != intent['templates']:
        raise ValueError('Live image inventory drifted; no scan launched')
    if args.action == 'target':
        if args.target not in intent['targets']:
            raise ValueError('Unknown frozen target')
        scan.request = request
        scan.SCANNER = scan.SCANNER.resolve()
        scan.ROOT = OUT / 'remaining-security-v1'
        scan.TARGETS = {k: tuple(v) for k, v in intent['targets'].items()}
        # Conservative three-ecosystem gate retained for all ACR families.
        sys.argv = [sys.argv[0], args.target]
        scan.main()
    elif args.action == 'batch':
        if any(OUT.glob('ibkrv2stg-*.log')):
            raise ValueError('Batch already started; inspect target evidence instead of redispatch')
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {pool.submit(run_one, name): name for name in intent['targets']}
            for future in as_completed(futures):
                try:
                    result = future.result()
                except Exception as exc:
                    result = {'target': futures[future], 'error_type': type(exc).__name__}
                print(json.dumps(result), flush=True)
    else:
        results = []
        for name in intent['targets']:
            path = OUT / 'remaining-security-v1' / ('image-scan-' + name + '-v1') / 'verification.json'
            if not path.exists():
                results.append({'target': name, 'status': 'incomplete'})
                continue
            value = json.loads(path.read_bytes())
            from rehearse_ms_worker_image_rollback import restore_protected
            remote, _ = restore_protected(value['protected_receipt'])
            if remote != value['record']:
                raise ValueError('Protected scan record differs')
            r = value['record']
            results.append({'target': name, 'image': r['image'], 'status': r['assessment']['status'],
                            'counts': r['counts'], 'assessment': r['assessment'], 'scan_receipt': value['protected_receipt']})
        if inventory() != intent['templates']:
            raise ValueError('Inventory changed during scan batch')
        record = {'results': results, 'deferred_references': intent['deferred_references'],
                  'runtime_templates_unchanged': True, 'full_upgrade_complete': False}
        with (OUT / 'scan-summary.json').open('x', encoding='utf-8') as handle:
            json.dump({**record, 'protected_receipt': retain(record, 'upgrade-stage19-scan-summary')}, handle, indent=2)
        print(json.dumps([{k: v for k, v in r.items() if k in ('target', 'status', 'counts')} for r in results]), flush=True)


if __name__ == '__main__':
    main()
