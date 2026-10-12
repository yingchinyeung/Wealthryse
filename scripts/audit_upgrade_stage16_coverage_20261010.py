"""Read-only post-reader-release exact-image scan coverage."""
import audit_upgrade_image_coverage_20261010 as audit
audit.OUT=audit.ROOT/'.work/platform-upgrade-20261010-stage16/image-coverage-v1.json'
audit.EXTRA_SCAN_FOLDERS=(audit.ROOT/'.work/platform-upgrade-20261010-stage14/legacy-installed-scan-v1',)
audit.RECEIPT_LABEL='upgrade-stage16-image-coverage'
if __name__=='__main__':audit.main()
