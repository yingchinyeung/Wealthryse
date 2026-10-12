"""Reuse the exact endpoint QA harness against the month-end worker's own v5 image."""
from pathlib import Path
root=Path(__file__).resolve().parents[1]
source=(root/'scripts/control_reporting_onboard_qa_20261011.py').read_text(encoding='utf-8')
changes={
    'import release_reporting_onboard_20261011 as release':'import release_reporting_history_20261011 as release',
    'platform-upgrade-20261011-stage36':'platform-upgrade-20261011-stage37',
    "OUT=STAGE/'onboard-runtime-v1'":"OUT=STAGE/'reporter-runtime-v1'",
    'candidate-stage36-onboard-v1/':'candidate-stage37-reporter-v1/',
    'for n in FILES}':'for n in (*FILES,"control_reporting_onboard_qa_20261011.py")}',
    'exact onboarding candidate':'exact month-end reporting candidate',
}
for before,after in changes.items():
    if source.count(before)!=(2 if before=='candidate-stage36-onboard-v1/' else 1):raise ValueError('Exact reporter QA anchor required: '+before)
    source=source.replace(before,after)
exec(compile(source,'versioned-month-end-worker-qa','exec'))
