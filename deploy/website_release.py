"""Release the website from GitHub Actions (see .github/workflows/deploy-website.yml).

Steps, each a subcommand so the workflow can show and stop at each one:
  prepare  read the live image, work out what changed, refuse unsupported changes
  build    build the new image on the private build VM through Azure Run Command
  deploy   point the website at the new image
  verify   wait for the new revision, check health and that sign-in is still required
  rollback point the website back at the image recorded by prepare
  assurance-build  build the analyst-assurance image (see build-assurance-image.yml);
                   the assurance job itself is not changed
State passes between steps in release-state.json.
"""
import base64, gzip, hashlib, json, subprocess, sys, tempfile, time, urllib.request, urllib.error
from pathlib import Path

RG = 'rg-ibkr-database-v11'
APP = 'ibkrv2stg-web'
VM = 'ibkrv2stg-private-admin'
REG = 'ibkrv2stgauhecwc4itaoo.azurecr.io'
# Commit the change is measured from before releases came from GitHub. The live
# files differ from it in places, so the build applies only the change on top of
# the live files and refuses if any hunk does not apply exactly.
BASELINE_COMMIT = '59dbd295c32b8503aa9f66be5b9cf0e5b70562b2'
PACKAGE = 'src/ibkr_etl'
# Paths that are not part of the website image.
IGNORED_PREFIXES = ('docs/', 'tests/', '.github/', 'infra/', 'deploy/', '.azuredevops/',
                    # CI-only helper; the website never runs it.
                    'scripts/ci_run_tests.py',
                    # Agent services ship in their own image (Dockerfile.agent).
                    'src/wealthryse_agent/', 'migrations/agent/', 'Dockerfile.agent',
                    'scripts/install_agent_schema.py')
STATE = Path('release-state.json')
SNAPSHOT = Path(__file__).resolve().parent / 'live-snapshot'
MERGED = Path(__file__).resolve().parent / 'live-merged'
ROOT = Path(__file__).resolve().parents[1]


def az(*args, parse=True):
    out = subprocess.run(['az', *args, '-o', 'json'] if parse else ['az', *args], check=True, capture_output=True, text=True).stdout
    return json.loads(out) if parse and out.strip() else out


def git(*args, binary=False):
    return subprocess.run(['git', *args], check=True, capture_output=True, cwd=ROOT, text=not binary).stdout


def package_files(commit):
    files = [f for f in git('ls-tree', '-r', '--name-only', commit, PACKAGE).split() if f.endswith('.py')]
    return {f[len(PACKAGE) + 1:]: git('show', commit + ':' + f, binary=True) for f in files}


def change_set(state):
    """The patch from what is live to what should be live, with before/after hashes.

    Files copied from the live image into deploy/live-snapshot/ go to their merged
    version in deploy/live-merged/ (live code plus our changes); every other file
    goes from the deployed commit to HEAD. Once a release from this repo is live,
    those files on live are the deployed commit's deploy/live-merged copies.
    """
    before, after = package_files(state['deployed_commit']), package_files(state['head'])
    snapshot = SNAPSHOT / PACKAGE
    if snapshot.exists():
        from_repo = state['deployed_commit'] != BASELINE_COMMIT
        if not from_repo and (SNAPSHOT / 'IMAGE').read_text().strip() != state['live_image']:
            raise SystemExit('deploy/live-snapshot was copied from a different live image; copy it again')
        for path in sorted(snapshot.glob('*.py')):
            merged = MERGED / PACKAGE / path.name
            if not merged.exists():
                raise SystemExit('No merged version of live ' + path.name + ' in deploy/live-merged')
            if from_repo:
                before[path.name] = git('show', state['deployed_commit'] + ':deploy/live-merged/' + PACKAGE + '/' + path.name, binary=True)
            else:
                before[path.name] = path.read_bytes().replace(b'\r\n', b'\n')
            after[path.name] = merged.read_bytes()
    changed = [n for n in set(before) | set(after) if before.get(n) != after.get(n)]
    with tempfile.TemporaryDirectory() as tmp:
        for side, files in (('a', before), ('b', after)):
            (Path(tmp) / side / PACKAGE).mkdir(parents=True)
            for name in changed:
                if name in files:
                    (Path(tmp) / side / PACKAGE / name).write_bytes(files[name])
        diff = subprocess.run(['git', 'diff', '--no-index', '--binary', '--no-prefix', 'a', 'b'], cwd=tmp, capture_output=True)
        if diff.returncode not in (0, 1):
            raise SystemExit('Could not compute the change: ' + diff.stderr.decode()[-500:])
    digest = lambda files: {n: hashlib.sha256(d).hexdigest() for n, d in files.items()}
    return diff.stdout, digest(before), digest(after)


def prepare():
    head = git('rev-parse', 'HEAD').strip()
    app = az('containerapp', 'show', '-n', APP, '-g', RG)
    containers = app['properties']['template']['containers']
    if len(containers) != 1:
        raise SystemExit('Expected one website container')
    live = containers[0]['image']
    tag = live.split(':')[-1] if '@' not in live else ''
    deployed = tag[len('web-'):] if tag.startswith('web-') and len(tag) == 44 else BASELINE_COMMIT
    if subprocess.run(['git', 'merge-base', '--is-ancestor', deployed, head], cwd=ROOT).returncode:
        raise SystemExit(f'Live website commit {deployed} is not in this branch history')
    changed = [p for p in git('diff', '--name-only', deployed, head).split() if p]
    # Scripts (Node report renderers and Python job/operator scripts) are run by
    # the reporting jobs or by hand, never by the website, and this path only
    # overlays src/ibkr_etl on live anyway, so they keep their live versions.
    skipped = [p for p in changed if p.startswith('scripts/') and p.endswith(('.mjs', '.py'))]
    if skipped:
        print('Not part of the website release (reporting scripts):\n  ' + '\n  '.join(skipped))
    image_changes = [p for p in changed if not p.startswith(IGNORED_PREFIXES) and not p.endswith('.md') and p not in skipped]
    unsupported = [p for p in image_changes if not p.startswith(PACKAGE + '/')]
    if unsupported:
        raise SystemExit('This release path only ships src/ibkr_etl. Other image files changed:\n  ' + '\n  '.join(unsupported))
    state = {
        'head': head, 'deployed_commit': deployed, 'live_image': live,
        'fqdn': app['properties']['configuration']['ingress']['fqdn'],
        'target_image': f'{REG}/ibkr-platform:web-{head}',
        'changed_files': [p for p in image_changes],
    }
    STATE.write_text(json.dumps(state, indent=2))
    print(json.dumps(state, indent=2))
    if not image_changes:
        print('No website source changes since the live image; nothing to release.')


def build():
    state = json.loads(STATE.read_text())
    if not state['changed_files']:
        return
    patch, live_hashes, new_hashes = change_set(state)
    config = {
        'base_image': state['live_image'], 'target_image': state['target_image'], 'source_commit': state['head'],
        'live_hashes': live_hashes, 'new_hashes': new_hashes,
        'patch': base64.b64encode(gzip.compress(patch, 9)).decode(),
    }
    code = (ROOT / 'deploy' / 'website_overlay_build_remote.py').read_text()
    marker = 'CONFIG = None  # replaced by the workflow'
    if code.count(marker) != 1:
        raise SystemExit('Build script placeholder missing')
    code = code.replace(marker, 'CONFIG = json.loads(' + repr(json.dumps(config)) + ')')
    command = "cat > /tmp/wealthryse-website-build.py <<'WEALTHRYSE_EOF'\n" + code + "\nWEALTHRYSE_EOF\n" \
              "unshare --mount --propagation private python3 /tmp/wealthryse-website-build.py; status=$?; rm -f /tmp/wealthryse-website-build.py; exit $status"
    vm = az('vm', 'show', '-g', RG, '-n', VM, '-d')
    started = vm.get('powerState') != 'VM running'
    if started:
        print('Starting the build VM')
        az('vm', 'start', '-g', RG, '-n', VM, parse=False)
    state['vm_started_by_release'] = started
    STATE.write_text(json.dumps(state, indent=2))
    sub = az('account', 'show')['id']
    base = f'https://management.azure.com/subscriptions/{sub}/resourceGroups/{RG}/providers/Microsoft.Compute/virtualMachines/{VM}/runCommands/'
    try:
        view = vm_run(base, 'website-build-' + state['head'][:12], command, vm['location'], 3000)
        output = view.get('output') or ''
        lines = [l[len('WEBSITE_BUILD_RESULT '):] for l in output.splitlines() if l.startswith('WEBSITE_BUILD_RESULT ')]
        result = json.loads(lines[-1]) if lines else {'passed': False, 'error': (view.get('error') or '')[-1500:],
                                                      'output_length': len(output), 'output_tail': output[-800:]}
        print(json.dumps(result, indent=2))
        if result.get('detail_file'):
            detail = read_detail(base, vm['location'], result['detail_file'], result['detail_size'])
            for name, text in json.loads(gzip.decompress(base64.b64decode(detail))).items():
                print('Live code where the change to ' + name + ' does not apply:\n' + text)
        if not result.get('passed') or view.get('exitCode') != 0:
            raise SystemExit('Build failed; the website was not changed')
        state['pushed_image'] = result['pushed_image']
        STATE.write_text(json.dumps(state, indent=2))
    finally:
        if started:
            print('Stopping the build VM again')
            subprocess.run(['az', 'vm', 'deallocate', '-g', RG, '-n', VM, '--no-wait'], capture_output=True)


def vm_run(base, name, script, location, timeout):
    """Run a script on the build VM through Run Command and return its instance view."""
    url = base + name + '?api-version=2024-07-01'
    body = Path('run-command.json')
    body.write_text(json.dumps({'location': location, 'properties': {
        'source': {'script': script}, 'asyncExecution': True, 'timeoutInSeconds': timeout, 'treatFailureAsDeploymentFailure': False}}))
    try:
        az('rest', '--method', 'put', '--url', url, '--body', '@' + str(body), parse=False)
        deadline = time.time() + timeout + 300
        while True:
            view = az('rest', '--method', 'get', '--url', url + '&$expand=instanceView')['properties'].get('instanceView') or {}
            if view.get('executionState') in ('Succeeded', 'Failed', 'TimedOut', 'Canceled'):
                return view
            if time.time() > deadline:
                raise SystemExit('Run Command did not finish in time')
            time.sleep(10)
    finally:
        subprocess.run(['az', 'rest', '--method', 'delete', '--url', url], capture_output=True)


def read_detail(base, location, path, size):
    """Run Command returns only 4 KB of output, so larger detail is read in pieces."""
    if not path.startswith('/var/tmp/wealthryse-build-detail-') or not path.endswith('.txt'):
        raise SystemExit('Unexpected detail file name')
    encoded = ''
    # Each Run Command can wait several minutes for the VM agent, so a short command still
    # gets a long deadline (a 7-minute one ran out on 13 Oct while reading an inventory).
    try:
        for offset in range(0, size, 4000):
            view = vm_run(base, f'website-detail-{offset // 4000}', f"tail -c +{offset + 1} {path} | head -c 4000", location, 900)
            encoded += (view.get('output') or '').strip()
    finally:
        vm_run(base, 'website-detail-cleanup', 'rm -f ' + path, location, 900)
    if len(encoded) != size:
        raise SystemExit(f'Read {len(encoded)} of {size} bytes of build detail')
    return encoded


IMAGE_PREFIX = REG + '/ibkr-platform@sha256:'


def remote_script(config):
    code = (ROOT / 'deploy' / 'website_overlay_build_remote.py').read_text()
    marker = 'CONFIG = None  # replaced by the workflow'
    if code.count(marker) != 1:
        raise SystemExit('Build script placeholder missing')
    code = code.replace(marker, 'CONFIG = json.loads(' + repr(json.dumps(config)) + ')')
    return "cat > /tmp/wealthryse-website-build.py <<'WEALTHRYSE_EOF'\n" + code + "\nWEALTHRYSE_EOF\n" \
           "unshare --mount --propagation private python3 /tmp/wealthryse-website-build.py; status=$?; rm -f /tmp/wealthryse-website-build.py; exit $status"


def read_only_on_vm(config, run_name):
    """Run a read-only capture or inventory on the build VM; return its result and detail."""
    vm = az('vm', 'show', '-g', RG, '-n', VM, '-d')
    started = vm.get('powerState') != 'VM running'
    if started:
        az('vm', 'start', '-g', RG, '-n', VM, parse=False)
    sub = az('account', 'show')['id']
    base = f'https://management.azure.com/subscriptions/{sub}/resourceGroups/{RG}/providers/Microsoft.Compute/virtualMachines/{VM}/runCommands/'
    try:
        view = vm_run(base, run_name, remote_script(config), vm['location'], 1800)
        lines = [l[len('WEBSITE_BUILD_RESULT '):] for l in (view.get('output') or '').splitlines() if l.startswith('WEBSITE_BUILD_RESULT ')]
        result = json.loads(lines[-1]) if lines else {'passed': False}
        print(json.dumps({k: v for k, v in result.items() if k not in ('captured', 'listing')}, indent=2))
        if not result.get('passed'):
            raise SystemExit('Read-only copy failed; nothing was changed')
        if not result.get('detail_file'):
            return result, ''
        return result, read_detail(base, vm['location'], result['detail_file'], result['detail_size'])
    finally:
        if started:
            subprocess.run(['az', 'vm', 'deallocate', '-g', RG, '-n', VM, '--no-wait'], capture_output=True)


def image_argument(args):
    """--image <registry image by digest> selects another deployed image; default is the website."""
    if args[:1] == ['--image']:
        if len(args) < 2 or not args[1].startswith(IMAGE_PREFIX) or len(args[1]) != len(IMAGE_PREFIX) + 64:
            raise SystemExit('Give --image as ' + IMAGE_PREFIX + '<64 hex digits>')
        return args[1], args[2:]
    return None, args


def capture():
    """Copy named files out of a deployed image (read-only).

    With no --image, the files come from the live website image into deploy/live-snapshot/
    (bare names, src/ibkr_etl only). With --image they go to deploy/image-captures/<digest>/
    under their repository paths (src/ibkr_etl/... or scripts/...).
    """
    image, names = image_argument(sys.argv[2:])
    if image is None:
        if not names or any('/' in n or not n.endswith('.py') for n in names):
            raise SystemExit('Name the src/ibkr_etl/*.py files to copy')
    elif not names or any(not n.endswith('.py') or '..' in n or not n.startswith(('src/ibkr_etl/', 'scripts/')) for n in names):
        raise SystemExit('Name src/ibkr_etl/... or scripts/... .py files to copy')
    head = git('rev-parse', 'HEAD').strip()
    live = image or az('containerapp', 'show', '-n', APP, '-g', RG)['properties']['template']['containers'][0]['image']
    config = {'capture': names, 'base_image': live, 'target_image': f'{REG}/ibkr-platform:web-capture',
              'source_commit': head, 'live_hashes': {}, 'new_hashes': {}, 'patch': ''}
    result, detail = read_only_on_vm(config, 'website-capture')
    archive = base64.b64decode(detail)
    import io, tarfile
    if image is None:
        target = ROOT / 'deploy' / 'live-snapshot' / PACKAGE
    else:
        target = ROOT / 'deploy' / 'image-captures' / image[len(IMAGE_PREFIX):][:12]
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:xz') as tar:
        for member in tar.getmembers():
            if member.name not in names or not member.isfile():
                raise SystemExit('Unexpected file in capture: ' + member.name)
            data = tar.extractfile(member).read()
            if hashlib.sha256(data).hexdigest() != result['captured'][member.name]:
                raise SystemExit('Captured file damaged in transfer: ' + member.name)
            (target / member.name).parent.mkdir(parents=True, exist_ok=True)
            (target / member.name).write_bytes(data)
    if image is None:
        (target.parent.parent / 'IMAGE').write_text(live + '\n')
    else:
        (target / 'IMAGE').write_text(live + '\n')
    print('Copied', ', '.join(names), 'from', live)


def repo_hashes(head):
    """SHA-256 of every .py file under src/ibkr_etl and scripts at head."""
    repo = {}
    for prefix in ('src/ibkr_etl/', 'scripts/'):
        for path in git('ls-tree', '-r', '--name-only', head, prefix).split():
            if path.endswith('.py'):
                repo[path] = hashlib.sha256(git('show', head + ':' + path, binary=True)).hexdigest()
    return repo


def compare_with_repo(hashes, head, repo=None):
    """Compare an image's file hashes with the repository at head."""
    repo = repo_hashes(head) if repo is None else repo
    scripts = any(p.startswith('scripts/') for p in hashes)
    repo = {p: h for p, h in repo.items() if scripts or not p.startswith('scripts/')}
    return {'same': sorted(p for p in hashes if repo.get(p) == hashes[p]),
            'different': sorted(p for p in hashes if p in repo and repo[p] != hashes[p]),
            'only_image': sorted(p for p in hashes if p not in repo),
            'only_repo': sorted(p for p in repo if p not in hashes)}


def listing_hashes(listing, repo):
    """Hashes for compare_with_repo from the VM's listing: the repository's own hash for
    files the VM found identical, and a marker for files that differ or are not in the repo."""
    differ = {listing['files'][i] for i in listing['differ']}
    return {name: 'differs' if name in differ else repo[name] for name in listing['files']}


def inventory():
    """Read-only: hash every .py file in deployed images and compare them with the repository.

    Writes deploy/image-inventory/<digest>.json per image; nothing in Azure changes.
    """
    images = sys.argv[2:]
    if not images or any(not i.startswith(IMAGE_PREFIX) or len(i) != len(IMAGE_PREFIX) + 64 for i in images):
        raise SystemExit('Give one or more images as ' + IMAGE_PREFIX + '<64 hex digits>')
    head = git('rev-parse', 'HEAD').strip()
    folder = ROOT / 'deploy' / 'image-inventory'
    folder.mkdir(parents=True, exist_ok=True)
    repo = repo_hashes(head)
    # The VM compares against these, so one Run Command per image is enough (the
    # follow-up reads of a detail file waited 20 minutes each on 13 Oct).
    short = base64.b64encode(gzip.compress(json.dumps({p: h[:16] for p, h in repo.items()}).encode(), 9)).decode()
    failed = []
    for image in dict.fromkeys(images):
        config = {'inventory': True, 'base_image': image, 'target_image': f'{REG}/ibkr-platform:web-capture',
                  'source_commit': head, 'live_hashes': {}, 'new_hashes': {}, 'patch': '', 'repo_hashes': short}
        try:
            result, detail = read_only_on_vm(config, 'image-inventory-' + image[len(IMAGE_PREFIX):][:12])
        except SystemExit as error:
            # One image failing still leaves the others worth keeping.
            print(image, 'FAILED:', error)
            failed.append(image)
            continue
        listing = json.loads(gzip.decompress(base64.b64decode(result.get('listing') or detail)))
        comparison = compare_with_repo(listing_hashes(listing, repo), head, repo)
        (folder / (image[len(IMAGE_PREFIX):][:12] + '.json')).write_text(json.dumps(
            {'image': image, 'repo_commit': head, 'files': listing['files'], **comparison}, indent=1, sort_keys=True) + '\n')
        print(image, {k: len(v) for k, v in comparison.items()})
    if failed:
        raise SystemExit('Inventory failed for ' + ' '.join(failed))


# The image ibkrv2stg-analyst-assurance ran on 13 Oct 2026. It passes MS and T,
# but expects the layout names of the reports it replaced; the reviewed reports
# released on 13 Oct carry the names below.
ASSURANCE_BASE = REG + '/ibkr-platform@sha256:0a5788c04f0202cb2fcdc84cd08c591043fd0ab633549e6858de95b400c3071f'
ASSURANCE_REPLACEMENTS = [
    {'file': 'monthly_artifacts.py', 'old': 'approved-august-2026-v8-return-panel-typography',
     'new': 'approved-august-2026-v9-full-portfolio-history'},
    {'file': 'monthly_artifacts.py', 'old': 'subject-completed-factsheet-model-v7-alpha-world-ms-authority',
     'new': 'subject-completed-factsheet-model-v8-full-portfolio-history'},
    {'file': None, 'old': 'platform-standard-v24-security-name-evidence-v7',
     'new': 'platform-standard-v24-reporting-currency-full-nav-v9'},
]
# Run inside the new image: the assurance check must expect the new names.
ASSURANCE_CHECK = """
import ibkr_etl.portfolio_assurance as a
want = {'U16088571': 'approved-august-2026-v9-full-portfolio-history',
        'U16088606': 'subject-completed-factsheet-model-v8-full-portfolio-history',
        'hong-kong-china': 'subject-completed-factsheet-model-v8-full-portfolio-history'}
for subject, layout in want.items():
    assert a.factsheet_layout_version(subject) == layout, subject
    assert a.presentation_content_contract_version(subject) == 'platform-standard-v24-reporting-currency-full-nav-v9', subject
print('assurance layouts checked')
"""


def assurance_config(head):
    return {'base_image': ASSURANCE_BASE, 'target_image': f'{REG}/ibkr-platform:assurance-layout-{head[:12]}',
            'source_commit': head, 'replacements': ASSURANCE_REPLACEMENTS, 'check': ASSURANCE_CHECK,
            'live_hashes': {}, 'new_hashes': {}, 'patch': ''}


def assurance_build():
    """Build and push the assurance image on the build VM. Nothing else changes."""
    head = git('rev-parse', 'HEAD').strip()
    code = (ROOT / 'deploy' / 'website_overlay_build_remote.py').read_text()
    marker = 'CONFIG = None  # replaced by the workflow'
    if code.count(marker) != 1:
        raise SystemExit('Build script placeholder missing')
    code = code.replace(marker, 'CONFIG = json.loads(' + repr(json.dumps(assurance_config(head))) + ')')
    command = "cat > /tmp/wealthryse-website-build.py <<'WEALTHRYSE_EOF'\n" + code + "\nWEALTHRYSE_EOF\n" \
              "unshare --mount --propagation private python3 /tmp/wealthryse-website-build.py; status=$?; rm -f /tmp/wealthryse-website-build.py; exit $status"
    vm = az('vm', 'show', '-g', RG, '-n', VM, '-d')
    started = vm.get('powerState') != 'VM running'
    if started:
        az('vm', 'start', '-g', RG, '-n', VM, parse=False)
    sub = az('account', 'show')['id']
    base = f'https://management.azure.com/subscriptions/{sub}/resourceGroups/{RG}/providers/Microsoft.Compute/virtualMachines/{VM}/runCommands/'
    try:
        view = vm_run(base, 'assurance-build-' + head[:12], command, vm['location'], 3000)
        output = view.get('output') or ''
        lines = [l[len('WEBSITE_BUILD_RESULT '):] for l in output.splitlines() if l.startswith('WEBSITE_BUILD_RESULT ')]
        result = json.loads(lines[-1]) if lines else {'passed': False, 'error': (view.get('error') or '')[-1500:],
                                                      'output_tail': output[-800:]}
        print(json.dumps(result, indent=2))
        if not result.get('passed') or view.get('exitCode') != 0:
            raise SystemExit('Assurance image build failed; nothing was changed')
        print('ASSURANCE IMAGE ' + result['pushed_image'])
    finally:
        if started:
            subprocess.run(['az', 'vm', 'deallocate', '-g', RG, '-n', VM, '--no-wait'], capture_output=True)


def deploy():
    state = json.loads(STATE.read_text())
    if not state['changed_files']:
        return
    now = az('containerapp', 'show', '-n', APP, '-g', RG)['properties']['template']['containers'][0]['image']
    if now != state['live_image']:
        raise SystemExit(f'Website image changed during the release ({now}); stopping')
    suffix = 'web-' + state['head'][:7] + '-' + str(int(time.time()))[-6:]
    az('containerapp', 'update', '-n', APP, '-g', RG, '--container-name', 'web', '--image', state['target_image'], '--revision-suffix', suffix, parse=False)
    state['revision'] = APP + '--' + suffix
    STATE.write_text(json.dumps(state, indent=2))
    print('Website switched to', state['target_image'])


def anonymous_status(fqdn, path):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open('https://' + fqdn + path, timeout=20) as r:
            return r.status, ''
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get('Location', '')


def verify():
    state = json.loads(STATE.read_text())
    if not state['changed_files']:
        return
    revision = state['revision']
    for _ in range(60):
        app = az('containerapp', 'show', '-n', APP, '-g', RG)['properties']
        if app.get('latestReadyRevisionName') == revision:
            break
        time.sleep(10)
    else:
        raise SystemExit('New revision did not become ready')
    rev = az('containerapp', 'revision', 'show', '-n', APP, '-g', RG, '--revision', revision)['properties']
    checks = []
    for path in ('/', '/api/portfolio/U16088571/holdings', '/api/holdings/combined', '/api/portfolio/U16088571/performance/periods'):
        code, location = anonymous_status(state['fqdn'], path)
        ok = code == 401 or (code == 302 and '/.auth/login/' in location)
        checks.append({'path': path, 'status': code, 'sign_in_required': ok})
    report = {'revision': revision, 'health': rev.get('healthState'), 'running': rev.get('runningState'), 'checks': checks}
    print(json.dumps(report, indent=2))
    if rev.get('healthState') != 'Healthy' or not all(c['sign_in_required'] for c in checks):
        raise SystemExit('Verification failed')


# Website chat settings (not secrets). The agent service is internal to the
# same Container Apps environment; the website signs in with its own identity.
AGENT_SETTINGS = {
    'AGENT_API_URL': 'https://ibkrv2stg-agent.internal.purplerock-8cff24a2.eastasia.azurecontainerapps.io',
    'AGENT_API_AUDIENCE': 'api://0578a50b-a7a1-40a4-905b-8b20dba84124/wealthryse-agent',
}


def chat():
    """Set AGENT_UI_MODE (off, owner or all) on the website; leaves the image alone."""
    mode = sys.argv[2]
    if mode not in ('off', 'owner', 'all'):
        raise SystemExit('Mode must be off, owner or all')
    app = az('containerapp', 'show', '-n', APP, '-g', RG)
    env = {e['name']: e.get('value') for e in app['properties']['template']['containers'][0].get('env', [])}
    print('AGENT_UI_MODE was', env.get('AGENT_UI_MODE', 'unset'))
    settings = {**AGENT_SETTINGS, 'AGENT_UI_MODE': mode} if mode != 'off' else {'AGENT_UI_MODE': 'off'}
    suffix = 'chat-' + mode + '-' + str(int(time.time()))[-6:]
    az('containerapp', 'update', '-n', APP, '-g', RG, '--container-name', 'web', '--revision-suffix', suffix,
       '--set-env-vars', *[k + '=' + v for k, v in settings.items()], parse=False)
    STATE.write_text(json.dumps({'changed_files': ['AGENT_UI_MODE'], 'revision': APP + '--' + suffix,
                                 'fqdn': app['properties']['configuration']['ingress']['fqdn'],
                                 'previous_mode': env.get('AGENT_UI_MODE', 'off')}, indent=2))
    print('Website chat set to', mode)


def rollback():
    state = json.loads(STATE.read_text())
    if 'previous_mode' in state:
        suffix = 'chat-rollback-' + str(int(time.time()))[-6:]
        az('containerapp', 'update', '-n', APP, '-g', RG, '--container-name', 'web', '--revision-suffix', suffix,
           '--set-env-vars', 'AGENT_UI_MODE=' + state['previous_mode'], parse=False)
        print('Website chat set back to', state['previous_mode'])
        return
    suffix = 'rollback-' + str(int(time.time()))[-6:]
    az('containerapp', 'update', '-n', APP, '-g', RG, '--container-name', 'web', '--image', state['live_image'], '--revision-suffix', suffix, parse=False)
    print('Website switched back to', state['live_image'])


if __name__ == '__main__':
    {'prepare': prepare, 'build': build, 'capture': capture, 'deploy': deploy, 'verify': verify, 'rollback': rollback, 'chat': chat,
     'assurance-build': assurance_build, 'inventory': inventory}[sys.argv[1]]()
