"""Qualify exact reference inputs under the real derived worker identity.

Downloads only the approved cache into disposable local storage; no provider,
database, workflow, report, permission or remote-storage mutations.
"""
import json
import sys
import tempfile
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = SCRIPT_ROOT.parent / "src"
for root in (SCRIPT_ROOT, SOURCE_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from ibkr_etl.config import load_settings
from ibkr_etl.reference_snapshot import load_reference_snapshot
from run_self_service_derived_refresh import _download_reference_data


def main():
    with tempfile.TemporaryDirectory() as folder:
        path=Path(folder)/"reference_data.json"
        receipt=_download_reference_data(load_settings(),path)
        _,manifest=load_reference_snapshot(path,expected_sha256=receipt["content_sha256"])
        if manifest["content_sha256"]!=receipt["storage"]["content_sha256"]:
            raise ValueError("Reference download and calculation bytes disagree")
    print("DERIVED_REFERENCE_INPUT_OBSERVATION "+json.dumps({"probe_complete":True,
        "reference_input":receipt,"calculation_manifest":manifest,
        "database_writes":0,"provider_requests":0,"source_changes":False,
        "report_approval_changes":False},sort_keys=True,allow_nan=False),flush=True)
    return 0


if __name__=="__main__":raise SystemExit(main())
