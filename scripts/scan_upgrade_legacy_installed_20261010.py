"""Fresh scan of the exact legacy daily image; no runtime changes."""
import shutil
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'.work/system-improvements-20261006')]
import scan_live_image as scan
from deploy_viewer_private_admin_20261008 import request

scan.request=request
scan.SCANNER=scan.SCANNER.resolve()
prior=scan.ROOT.resolve()
scan.ROOT=ROOT/'.work/platform-upgrade-20261010-stage14/legacy-installed-scan-v1'
scan.REGISTRY='ibkrv115rcc62xxqblay.azurecr.io'
scan.TARGETS={'legacy':('Microsoft.App/jobs/ibkr-v11-daily','ibkr-etl','sha256:0d0eb9105de73b9339bcdbc58a5c05069177f010b21dd13c9bd2f77ff0a7ca91')}
original=scan.assess_container_scan
def assessment(**kwargs):
    # The legacy ETL requires Debian and Python coverage. Every detected package
    # ecosystem is still assessed; no finding/severity is excluded or waived.
    kwargs['required_types']=frozenset(('debian','python-pkg'))
    return original(**kwargs)
scan.assess_container_scan=assessment

if __name__=='__main__':
    if shutil.disk_usage(ROOT).free<12*1024**3:raise ValueError('Insufficient safe scan space')
    scan.ROOT.mkdir(exist_ok=True)
    if not (scan.ROOT/'scanner-cache').exists():shutil.copytree(prior/'scanner-cache',scan.ROOT/'scanner-cache')
    scan.main()
