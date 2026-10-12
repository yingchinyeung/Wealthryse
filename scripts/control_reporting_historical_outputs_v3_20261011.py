"""Same v4 renderer and independent audits, with the qualified August snapshot."""
from pathlib import Path
root=Path(__file__).resolve().parents[1]
source=(root/'scripts/control_reporting_historical_outputs_v2_20261011.py').read_text()
changes={
    "FILES=[Path(__file__).name,":"FILES=[Path(__file__).name,'control_reporting_historical_outputs_v2_20261011.py',",
    "+'-v2')":"+'-v3')",
    "migration=protected(STAGE/'monthly-snapshot-enrichment-v1/result.json')":"migration=protected(STAGE/'august-snapshot-correction-v1/result.json')",
    "        if len(expected)!=21 or review['image']!=migration['image']:":"        august=protected(STAGE/'august-monthly-plan-v1/result.json')\n        if not august['passed']:raise ValueError('Qualified August plan required')\n        if subject=='U16088606':expected['2026-08-31']=august['proof']['candidate_sha256']\n        if len(expected)!=21 or review['image']!=migration['image']:",
    "'candidate-stage34-historical-v2/'":"'candidate-stage34-historical-v3/'",
}
for before,after in changes.items():
    if source.count(before)!=1:raise ValueError('Exact historical qualification anchor required: '+before)
    source=source.replace(before,after)
if __name__=='__main__':exec(compile(source,'qualified-historical-v3','exec'))
