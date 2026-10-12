"""Reuse the qualified image-only deployment/fallback guard for one job."""
import sys
import control_upgrade_onboarding_runtime_20261010 as control
import deploy_upgrade_administration_security_20261010 as deploy
deploy.c=control
if __name__=='__main__':deploy.main('onboarding',sys.argv[1])
