"""Read-only scans of exact installed administrative scheduled images."""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / '.work/system-improvements-20261006')]
import scan_live_image as scan
from deploy_viewer_private_admin_20261008 import request

scan.request = request
scan.SCANNER = scan.SCANNER.resolve()
prior = scan.ROOT.resolve()
scan.ROOT = ROOT / '.work/platform-upgrade-20261010-stage18/scheduled-security-v1'
scan.TARGETS = {
    'autonomous-admin': ('Microsoft.App/jobs/ibkrv2stg-autonomous-admin', 'ibkr-portfolio',
        'sha256:a77ecaac7528f4c1837bbeda130156d9253ae7cf9b2dcc9e2f2ba71b32da92e0'),
    'autonomy-review': ('Microsoft.App/jobs/ibkrv2stg-autonomy-review', 'ibkr-portfolio',
        'sha256:73ea7fc70a90bfd576e575b45d5f2dadbab467393616797dea1d2464c0ff217e'),
}

if __name__ == '__main__':
    if shutil.disk_usage(ROOT).free < 24 * 1024**3:
        raise ValueError('Insufficient safe scan space')
    scan.ROOT.mkdir(parents=True, exist_ok=True)
    if not (scan.ROOT / 'scanner-cache').exists():
        shutil.copytree(prior / 'scanner-cache', scan.ROOT / 'scanner-cache')
    scan.main()
