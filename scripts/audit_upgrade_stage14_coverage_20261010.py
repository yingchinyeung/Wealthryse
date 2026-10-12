"""Read-only post-deployment inventory including the fresh legacy scan."""
import audit_upgrade_image_coverage_20261010 as audit
audit.OUT=audit.ROOT/'.work/platform-upgrade-20261010-stage14/image-coverage-v1.json'
audit.EXTRA_SCAN_FOLDERS=(audit.OUT.parent/'legacy-installed-scan-v1',)
audit.RECEIPT_LABEL='upgrade-stage14-image-coverage'
if __name__=='__main__':audit.main()
