"""Read-only immutable-image scans of remaining continuously used families."""
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
scan.ROOT = ROOT / '.work/platform-upgrade-20261010-stage12/operational-security-v1'
scan.TARGETS = {
    'broker': ('Microsoft.App/containerApps/ibkrv2stg-credential-broker', 'ibkr-platform',
               'sha256:8575b64cb212e4ba32de57f00bc21b045320974b91fceacdce9aace8008416d7'),
    'metadata': ('Microsoft.App/jobs/ibkrv2stg-metadata-monitor', 'ibkr-platform',
                 'sha256:276a4783bf4ddf437bd5d449e08df39729fb43f449975082af80fd4c13e1142c'),
    'riskfree': ('Microsoft.App/jobs/ibkrv2stg-risk-free-daily', 'ibkr-platform',
                 'sha256:06c44ff756bbc7da0f41f14c9bfe08fff2ecb16e06a94c8576654bf176a00038'),
    'institutional': ('Microsoft.App/jobs/ibkrv2stg-institutional-daily', 'ibkr-etl',
                      'sha256:44472829f7be06003fd34d0b188794c05003c075c7c0e500e84b523c0808c405'),
}

if __name__ == '__main__':
    if shutil.disk_usage(ROOT).free < 12 * 1024**3:
        raise ValueError('Insufficient safe local scan space')
    scan.ROOT.mkdir(exist_ok=True)
    if not (scan.ROOT / 'scanner-cache').exists():
        shutil.copytree(prior / 'scanner-cache', scan.ROOT / 'scanner-cache')
    scan.main()
