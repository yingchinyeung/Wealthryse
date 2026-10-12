import sys
import control_platform_assurance_probe_20261009 as control
control.SOURCE=control.ROOT/'scripts/probe_ms_upgrade_postflight_20261009.py'
control.OUT=control.ROOT/'.work/platform-upgrade-20261009-stage5/live-postflight-v1'
if __name__=='__main__':control.main(sys.argv[1])
