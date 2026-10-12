"""Use existing image-only deployment guards with explicit two-workflow scope."""
import sys
import deploy_upgrade_administration_security_20261010 as deploy
import control_upgrade_workflow_runtime_20261010 as workflow
from types import SimpleNamespace


# Preserve the tested generic deployment implementation. Its three-item target
# contract is adapted without changing workflow controller configuration.
deploy.c=SimpleNamespace(**{name:getattr(workflow,name) for name in
    ('OUT','API','candidate','accept','protected','request','rid','settings_invariant','save','runtime')},
    TARGETS={name:(spec['job'],spec['entry'],spec['digest']) for name,spec in workflow.TARGETS.items()})


if __name__=='__main__':deploy.main(*sys.argv[1:])
