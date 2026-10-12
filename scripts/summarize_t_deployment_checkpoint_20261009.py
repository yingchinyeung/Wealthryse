"""Append verified deployment evidence without replacing prior checkpoints."""
import hashlib
import html
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output/audit/AlphaWorld_T_Onboarding_20261009'


def main():
    prior = json.loads((OUT / 'Checkpoint_v5.json').read_bytes())
    folder = ROOT / '.work/alpha-t-runtime-cutover-20261009-v1'
    completion = json.loads((folder / 'completion.json').read_bytes())
    native_path = ROOT / '.work/alpha-t-candidate-reports-20261009-v4/native-review-2026-09-30.json'
    native = json.loads(native_path.read_bytes())
    assert native['slides_individually_inspected'] == list(range(1, 21))
    records = {p.stem: json.loads(p.read_bytes()) for p in folder.glob('*-verified.json')}
    assert len(records) == 6
    prior.update(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status='Six runtime targets deployed and verified; T onboarding not complete',
        supersedes_status_in='Checkpoint_v5.json; previous evidence retained',
        deployment_completion=completion,
        deployment_verifications=records,
        native_powerpoint_review=native,
        live_services_updated_in_this_phase=True,
        remaining=[
            'Approve the exact HKD definition through the governed owner workflow, then persist and reconcile T analytics.',
            'Finish all-month visual acceptance and register the private historical report batch.',
            'Complete website download, report approval and viewer-access checks before declaring onboarding complete.',
        ],
    )
    for candidate in prior['candidate_images'].values():
        candidate['deployed'] = True
    for path in [folder / 'completion.json', native_path, *folder.glob('*-verified.json')]:
        prior['evidence'].append({'file':path.relative_to(ROOT).as_posix(),
                                  'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    with (OUT / 'Checkpoint_v6.json').open('x', encoding='utf-8') as stream:
        json.dump(prior, stream, indent=2)
    body = '<h1>Alpha World T deployment checkpoint</h1><p><strong>Runtime deployed; onboarding is not complete.</strong></p>'
    body += '<p>The website and five jobs use the four qualified images. Settings and authentication were preserved. T remains private and inactive; report approvals and viewer access have not changed.</p>'
    body += '<p>All 21 PDF and 21 PowerPoint files passed automated financial checks and hash-verified download. All 20 slides of the final September Azure PowerPoint were individually inspected. This is not a claim that all 420 monthly slides were manually reviewed.</p>'
    body += '<ol>' + ''.join('<li>' + html.escape(s) + '</li>' for s in prior['remaining']) + '</ol>'
    body += '<details><summary>Evidence</summary><pre>' + html.escape(json.dumps(prior, indent=2)) + '</pre></details>'
    with (OUT / 'Checkpoint_v6.html').open('x', encoding='utf-8') as stream:
        stream.write('<!doctype html><meta charset="utf-8"><title>Alpha World T checkpoint 6</title><style>body{max-width:1000px;margin:40px auto;font:16px/1.55 system-ui}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>' + body)
    print(json.dumps({'checkpoint':str(OUT / 'Checkpoint_v6.html'),'onboarding_complete':False}))


if __name__ == '__main__':
    main()
