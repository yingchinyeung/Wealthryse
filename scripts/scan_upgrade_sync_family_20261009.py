"""Scan exact current broker-sync image only; never execute it or call IBKR."""
import sys,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'.work/system-improvements-20261006')]
import scan_live_image as scan
from deploy_viewer_private_admin_20261008 import request
from ibkr_etl.container_scan import assess_container_scan
scan.request=request
scan.SCANNER=scan.SCANNER.resolve()
prior=scan.ROOT.resolve();scan.ROOT=ROOT/'.work/platform-upgrade-20261009-stage11/security-families-v1'
scan.TARGETS={'sync':('Microsoft.App/jobs/ibkrv2stg-account-sync-daily','ibkr-etl','sha256:39f73f592d98432b1c1912f57008c2171a591b3c851468ebd20fd97e4a8bdb81')}
def assess(**kwargs):
    # This Python ETL image does not require the reporting image's Node runtime.
    kwargs['required_types']=frozenset(('debian','python-pkg'))
    return assess_container_scan(**kwargs)
scan.assess_container_scan=assess
if __name__=='__main__':
    if shutil.disk_usage(ROOT).free<12*1024**3:raise ValueError('Insufficient safe local scan space')
    scan.ROOT.mkdir(exist_ok=True)
    cache=scan.ROOT/'scanner-cache'
    if not cache.exists():shutil.copytree(prior/'scanner-cache',cache)
    scan.main()
