"""Identical guard suite with reporter-specific receipt scope."""
from pathlib import Path
root=Path(__file__).resolve().parents[1]
source=(root/'scripts/test_reporting_onboard_qa_20261011.py').read_text(encoding='utf-8')
source=source.replace('control_reporting_onboard_qa_20261011','control_reporting_reporter_qa_20261011').replace('candidate-stage36-onboard-v1/','candidate-stage37-reporter-v1/')
exec(compile(source,'reporter-qa-guards','exec'))
