"""Replay the shared worker output gate on a visually qualified private batch.

No Azure, database, source-data, release or onboarding writes. Creates one
immutable local qualification receipt, distinct from the old render evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from ibkr_etl.presentation_design import presentation_content_contract_version
from validate_subject_presentation import validate_subject_presentation


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch', type=Path, required=True)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args()
    financial_path = args.batch / 'batch-financial-package-receipt.json'
    visual_path = args.batch / 'visual-review-coverage.json'
    financial = json.loads(financial_path.read_bytes())
    visual = json.loads(visual_path.read_bytes())
    if (financial['status'] != 'financial_package_passed' or financial['months_verified'] != 21
            or financial['errors'] or visual['status'] != 'rendered_visual_passed'
            or visual['covered_slides'] != 420 or visual['pending_slides'] != 0
            or visual['financial_package_receipt_sha256'] != sha(financial_path)):
        raise ValueError('A complete financial and rendered-visual batch is required')
    sources = (
        'scripts/validate_subject_presentation.py', 'scripts/audit_alpha_ms_presentation.py',
        'scripts/validate_monthly_presentation.py', 'src/ibkr_etl/presentation_design.py',
        'src/ibkr_etl/alpha_world_ms_presentation.py', 'scripts/render_monthly_presentation.mjs',
        'scripts/render_onboarding_historical_reports.py', 'scripts/generate_month_end_package.py',
        'scripts/run_self_service_onboarding_reports.py', 'src/ibkr_etl/portfolio_assurance.py',
        'scripts/qualify_ms_worker_presentation_gate.py',
    )
    binding = {source: sha(ROOT / source) for source in sources}
    visual_by_period = {item['period_end']: item for item in visual['items']}
    items = []
    for item in financial['items']:
        snapshot = args.inputs / (item['period_end'] + '.json')
        output = Path(item['output_path'])
        if (sha(snapshot) != item['input_sha256'] or sha(output) != item['output_sha256']
                or visual_by_period[item['period_end']]['output_sha256'] != sha(output)):
            raise ValueError('Qualified input or output changed before worker validation')
        for slide in visual_by_period[item['period_end']]['slides']:
            if sha(Path(item['preview_folder']) / f"slide-{slide['slide']:02d}.png") != slide['preview_sha256']:
                raise ValueError('Reviewed final preview changed')
        result = validate_subject_presentation(output, snapshot, subject_id='alpha-world-ms',
            period_end=item['period_end'], expected_strategy='iStrategy: Alpha World MS')
        if (result['snapshot_sha256'] != item['input_sha256']
                or result['output_sha256'] != item['output_sha256']):
            raise ValueError('Worker gate lost frozen input/output binding')
        items.append({'period_end': item['period_end'], 'validation': result})
    if binding != {source: sha(ROOT / source) for source in sources}:
        raise ValueError('Worker qualification code changed during replay')
    record = {
        'status': 'local_worker_output_gate_passed', 'months_verified': len(items),
        'content_contract_version': presentation_content_contract_version('alpha-world-ms'),
        'financial_receipt_sha256': sha(financial_path), 'visual_receipt_sha256': sha(visual_path),
        'code_binding': binding, 'items': items,
        'scope': 'Both workers share this gate; no end-to-end cloud worker execution is claimed',
        'azure_changed': False, 'source_data_changed': False, 'reports_released': False,
    }
    if args.receipt.exists():
        if json.loads(args.receipt.read_bytes()) != record:
            raise ValueError('Preserve existing evidence; use a new receipt revision')
    else:
        with args.receipt.open('x', encoding='utf-8') as stream:
            stream.write(json.dumps(record, indent=2) + '\n')
    print(json.dumps({key: value for key, value in record.items() if key not in ('items', 'code_binding')}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
