"""Bind four actual authenticated browser downloads to the private registry."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / '.work/alpha-t-registered-reports-20261009-v2'
DOWNLOADS = ROOT.parents[1]
FILES = {
    1090: 'alpha-world-t_Factsheet_2025-01-31_49ab72af776501b091c6c9e6e089cb21e8997235a8339de0c0caf2de8453a358 (1).pdf',
    1049: 'alpha-world-t_Monthly_Presentation_2025-01-31_cf2235e534d725979d5e23301b2c7d312de21a2665957d7521d86230c13d08fe.pptx',
    1110: 'alpha-world-t_Factsheet_2026-09-30_1dd7cb8a7f184267c0320411566da3dc81a9464b000c1f630c8e219b8b8a6778.pdf',
    1089: 'alpha-world-t_Monthly_Presentation_2026-09-30_cb7a0c08965bd791f93fc6c809cffdecf298bd50f6984dc0cd3bef5d786586c6.pptx',
}
registry = json.loads((FOLDER / 'registry.json').read_bytes())
rows = {int(row['generation_run_id']): row for row in registry['items']}
checks = []
for run_id, name in FILES.items():
    path = DOWNLOADS / name
    content = path.read_bytes()
    row = rows[run_id]
    digest = hashlib.sha256(content).hexdigest()
    assert digest == row['artifact_sha256'] and len(content) == row['byte_count']
    checks.append({'generation_run_id': run_id, 'period_end': row['period_end'],
                   'report_type': row['report_type'], 'sha256': digest,
                   'local_file': str(path), 'registered_hash_matches': True})
result = {'status': 'passed', 'batch_id': registry['batch']['batch_id'],
          'browser_files_verified': len(checks), 'checks': checks,
          'coverage': 'First and latest monthly PDF and PPTX, not all 42 browser downloads',
          'in_progress_404': 'Downloads are gated until the batch reaches awaiting_owner_approval; completed batch downloads succeeded.'}
with (FOLDER / 'browser-download-validation.json').open('x', encoding='utf-8') as stream:
    json.dump(result, stream, indent=2)
print(json.dumps(result))
