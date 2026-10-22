"""Weekly read-only check that what runs in Azure is what the repo records.

Compares every ibkrv2stg container app and job image with deploy/deployed-images.json,
checks the website runs a web-<commit> build from main, and that the assurance and
month-end jobs still run the images deploy/report-layouts.json was checked against.
Only reads (az containerapp list / job list); nothing in Azure changes.
Exits 1 and lists the differences when anything drifted.
"""
import json, subprocess, sys
from pathlib import Path

RG = 'rg-ibkr-database-v11'
PREFIX = 'ibkrv2stg-'
WEBSITE = 'ibkrv2stg-web'
ROOT = Path(__file__).resolve().parents[1]


def az(*args):
    run = subprocess.run(['az', *args, '-o', 'json'], capture_output=True, text=True)
    if run.returncode:
        raise SystemExit('Could not read ' + ' '.join(args[:3]) + ' (the identity may lack read access): ' + run.stderr[-500:])
    return json.loads(run.stdout or '[]')


def deployed_images():
    images = {}
    for kind in (('containerapp', 'list'), ('containerapp', 'job', 'list')):
        for item in az(*kind, '-g', RG):
            if item['name'].startswith(PREFIX):
                containers = item['properties']['template']['containers']
                images[item['name']] = containers[0]['image'] if len(containers) == 1 else sorted(c['image'] for c in containers)
    return images


def in_main(commit):
    return subprocess.run(['git', 'merge-base', '--is-ancestor', commit, 'HEAD'], cwd=ROOT, capture_output=True).returncode == 0


def website_behind(commit):
    out = subprocess.run(['git', 'log', '--oneline', commit + '..HEAD', '--', 'src/ibkr_etl'], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line]


def compare(live, recorded, layouts, in_main=in_main):
    """Differences between live and recorded images, as readable lines."""
    problems = []
    for name in sorted(set(live) | set(recorded)):
        if name == WEBSITE:
            continue
        if name not in live:
            problems.append(f'{name}: recorded but no longer deployed')
        elif name not in recorded:
            problems.append(f'{name}: deployed but not recorded ({live[name]})')
        elif live[name] != recorded[name]:
            problems.append(f'{name}: runs {live[name]}, recorded {recorded[name]}')
    web = live.get(WEBSITE)
    tag = web.rsplit(':', 1)[-1] if isinstance(web, str) and '@' not in web else ''
    if not (tag.startswith('web-') and len(tag) == 44):
        problems.append(f'{WEBSITE}: runs {web}, not a web-<commit> build from this repo')
    elif not in_main(tag[4:]):
        problems.append(f'{WEBSITE}: runs commit {tag[4:]}, which is not in main')
    for job, key in ((layouts['assurance_job'], 'assurance_image'), (layouts['month_end_job'], 'month_end_image')):
        if live.get(job) != layouts[key]:
            problems.append(f'{job}: runs {live.get(job)}, but report-layouts.json was checked against {layouts[key]}')
    return problems


def main():
    recorded = json.loads((ROOT / 'deploy' / 'deployed-images.json').read_text())['images']
    layouts = json.loads((ROOT / 'deploy' / 'report-layouts.json').read_text())
    live = deployed_images()
    problems = compare(live, recorded, layouts)
    web = live.get(WEBSITE, '')
    if isinstance(web, str) and ':web-' in web and in_main(web.rsplit(':web-', 1)[1]):
        behind = website_behind(web.rsplit(':web-', 1)[1])
        print(f'Website runs {web.rsplit(":", 1)[1]}; {len(behind)} website commits on main since then')
        for line in behind:
            print('  ' + line)
    print(f'{len(live)} ibkrv2stg apps and jobs read')
    if problems:
        print('DRIFT:\n  ' + '\n  '.join(problems))
        raise SystemExit(1)
    print('No drift: every image matches the repo record')


if __name__ == '__main__':
    main()
