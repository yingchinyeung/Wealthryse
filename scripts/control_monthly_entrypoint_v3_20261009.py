"""Qualify the v3 image through the read-only real monthly registry path."""
import sys
import control_monthly_entrypoint_probe_20261009 as controller
import release_platform_worker_stage3_v3_20261009 as release

controller.worker_release = release
controller.SOURCE = controller.SOURCE.with_name('probe_monthly_worker_entrypoint_v2_20261009.py')
controller.probe.release.WEB = release.release.WEB
controller.probe.OUT = release.BUNDLE / 'entrypoint-probe-v3'

if __name__ == '__main__':
    controller.probe.main(sys.argv[1])
