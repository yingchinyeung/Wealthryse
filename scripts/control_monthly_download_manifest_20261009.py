import sys
from pathlib import Path
import control_platform_upgrade_probe_20261009 as probe
import release_platform_worker_stage3_v3_20261009 as installed
probe.release.WEB=installed.release.WEB
probe.SOURCE=Path(__file__).with_name('probe_monthly_download_manifest_20261009.py')
probe.OUT=installed.BUNDLE/'download-manifest-v1'
if __name__=='__main__':probe.main(sys.argv[1])
