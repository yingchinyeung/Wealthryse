"""Join live image inventory to both protected candidate and installed scans."""
import collections
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import audit_upgrade_image_coverage_20261009 as prior
from rehearse_ms_worker_image_rollback import restore_protected
from run_specialist_gateway_read_verification import retain

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.work/platform-upgrade-20261010-stage13/image-coverage-v1.json'
EXTRA_SCAN_FOLDERS = ()
RECEIPT_LABEL = 'upgrade-stage13-image-coverage'


def main():
    if OUT.exists(): raise ValueError('Preserve prior coverage')
    rows = prior.inventory(); images = {r['image'] for r in rows}
    found = collections.defaultdict(list)
    paths = subprocess.run(['rg', '--files', '--hidden', '.work', '-g', 'scan-completed.json',
                            '-g', '!node_modules'], cwd=ROOT, text=True, capture_output=True, check=True).stdout.splitlines()
    for name in paths:
        path = ROOT / name; record = json.loads(path.read_bytes())
        receipt = path.with_name('scan-protected-receipt.json')
        if record.get('image') in images and receipt.exists():
            found[record['image']].append((path, record, json.loads(receipt.read_bytes())))
    folder = ROOT / '.work/platform-upgrade-20261010-stage12/operational-security-v1'
    for scan_folder in (folder, *EXTRA_SCAN_FOLDERS):
        for path in scan_folder.glob('image-scan-*/verification.json'):
            value = json.loads(path.read_bytes()); record = value['record']
            if record.get('image') in images:
                found[record['image']].append((path, record, value['protected_receipt']))
    checks = []
    for image in sorted(images):
        item = {'image': image, 'templates': [r['name'] for r in rows if r['image'] == image],
                'protected_scan_verified': False}
        candidates = sorted(found.get(image, []), key=lambda x: x[0].stat().st_mtime, reverse=True)
        if candidates:
            path, record, receipt = candidates[0]
            remote, _ = restore_protected(receipt)
            if remote != record: raise ValueError('Protected scan differs')
            a = record['assessment']
            if a['image_digest'] != image.split('@')[-1]: raise ValueError('Scan image differs')
            item.update(protected_scan_verified=True, scan_path=str(path.relative_to(ROOT)),
                        status=a['status'], counts=a['counts'], scan_receipt=receipt,
                        scanner_version=a.get('scanner_version'))
        checks.append(item)
    if prior.inventory() != rows: raise ValueError('Inventory drift during audit')
    result = {'checked_at': datetime.now(timezone.utc).isoformat(), 'definitions': len(rows),
              'unique_images': len(images), 'templates': rows, 'images': checks,
              'passing_scan_images': sum(i.get('status') == 'pass' for i in checks),
              'blocked_scan_images': sum(i.get('status') == 'blocked' for i in checks),
              'unmatched_protected_scan_images': sum(not i['protected_scan_verified'] for i in checks),
              'scope': 'Existing exact-image protected scan coverage, not a new scan of every image. Unmatched does not mean never scanned. No runtime changes.',
              'full_upgrade_complete': False}
    with OUT.open('x', encoding='utf-8') as f:
        json.dump({**result, 'protected_receipt': retain(result, RECEIPT_LABEL)}, f, indent=2)
    print(json.dumps({k: v for k, v in result.items() if k not in ('templates', 'images')}))


if __name__ == '__main__': main()
