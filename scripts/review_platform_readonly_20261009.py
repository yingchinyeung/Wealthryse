"""Fresh read-only audit; never invoke deployment, repair, job-start or data-write code."""
import importlib
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-review-20261009-v1'
OUT.mkdir(exist_ok=True)
import audit_viewer_resource_group_20261008 as inventory
inventory.FOLDER=OUT
modules={'inventory':inventory,
         'security':importlib.import_module('audit_viewer_security_metadata_20261008'),
         'roles':importlib.import_module('audit_viewer_roles_20261008'),
         'capacity':importlib.import_module('audit_viewer_capacity_metadata_20261008'),
         'logs':importlib.import_module('read_viewer_runtime_failures_20261008')}
if sys.argv[1] not in modules:raise ValueError('Read-only audit phase required')
modules[sys.argv[1]].FOLDER=OUT
modules[sys.argv[1]].main()
