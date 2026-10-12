"""Current image evidence after scheduled-administration image scans."""
import audit_upgrade_image_coverage_20261010 as audit
audit.OUT = audit.ROOT / '.work/platform-upgrade-20261010-stage18/image-coverage-v1.json'
audit.EXTRA_SCAN_FOLDERS = (
    audit.ROOT / '.work/platform-upgrade-20261010-stage14/legacy-installed-scan-v1',
    audit.ROOT / '.work/platform-upgrade-20261010-stage18/scheduled-security-v1',
)
audit.RECEIPT_LABEL = 'upgrade-stage18-image-coverage'
if __name__ == '__main__': audit.main()
