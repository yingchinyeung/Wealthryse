"""Build/inspect only the frozen T candidate; never update running services."""
import sys
import release_alpha_ms_headers_v14r15 as native
from prepare_t_runtime_release_20261009 import FOLDER, IMAGES

if __name__ == '__main__':
    if len(sys.argv)!=3 or sys.argv[1] not in ('build','inspect','log') or sys.argv[2] not in IMAGES:
        raise ValueError('Expected build|inspect|log web|worker')
    native.FOLDER=FOLDER
    native.IMAGES=IMAGES
    native.MARKER='PASS: qualified T HKD runtime candidate'
    native.native(sys.argv[1],sys.argv[2])
