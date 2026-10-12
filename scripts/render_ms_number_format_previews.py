"""Read-only private previews and exact prior-reference comparisons for 21 MS decks."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import subprocess


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def render(candidate, reference, output, node):
    proof = json.loads((candidate / 'qualification.json').read_bytes())
    if proof['status'] != 'private_number_format_package_and_financial_checks_passed' or len(proof['items']) != 21:
        raise ValueError('Expected the qualified private 21-month formatting batch')
    output.mkdir(exist_ok=False)
    helper = Path(__file__).with_name('inspect_alpha_ms_presentation.mjs')
    helper_hash = sha(helper)
    environment = dict(os.environ)
    def one(item):
        source = Path(item['output_file'])
        if sha(source) != item['output_sha256']:
            raise ValueError('Candidate changed before preview')
        period = item['period_end']
        folder = output / period
        process = subprocess.run([str(node), str(helper), str(source), str(folder)],
                                 env=environment, capture_output=True, text=True, timeout=120)
        if process.returncode:
            raise ValueError(f'{period}: preview failed: {process.stderr[:1000]}')
        rendered = json.loads(process.stdout.strip().splitlines()[-1])
        if rendered['slides'] != 20:
            raise ValueError('Twenty preview slides required')
        comparisons = []
        for index in range(1, 21):
            name = f'slide-{index:02d}.png'
            old, new = reference / period / 'slides' / name, folder / name
            comparisons.append({'slide': index, 'prior_sha256': sha(old), 'new_sha256': sha(new),
                                'identical': sha(old) == sha(new)})
        if sha(source) != item['output_sha256']:
            raise ValueError('Candidate changed during preview')
        return {'period_end': period, 'presentation_sha256': item['output_sha256'],
                'previews': comparisons, 'changed_slides': [r['slide'] for r in comparisons if not r['identical']]}
    results = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(one, item) for item in proof['items']]
        for future in as_completed(jobs):
            item = future.result()
            results.append(item)
            print(json.dumps({'completed': len(results), 'period_end': item['period_end'],
                              'changed_slides': item['changed_slides']}), flush=True)
    if sha(helper) != helper_hash:
        raise ValueError('Preview helper changed during run')
    result = {'status': 'all_month_previews_rendered_visual_delta_review_pending', 'months': len(results),
              'slides_rendered': 20 * len(results), 'helper_sha256': helper_hash,
              'candidate_proof_sha256': sha(candidate / 'qualification.json'),
              'pixel_identical_slides': sum(r['identical'] for item in results for r in item['previews']),
              'visual_review_complete': False, 'azure_changed': False,
              'items': sorted(results, key=lambda r: r['period_end'])}
    with (output / 'comparison.json').open('x', encoding='utf-8') as handle:
        json.dump(result, handle, indent=2, allow_nan=False)
    print(json.dumps({k: v for k, v in result.items() if k != 'items'}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('candidate', 'reference', 'output', 'node'):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    render(args.candidate, args.reference, args.output, args.node)
