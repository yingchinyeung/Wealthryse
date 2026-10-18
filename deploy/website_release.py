"""Release the website from GitHub Actions (see .github/workflows/deploy-website.yml).

Steps, each a subcommand so the workflow can show and stop at each one:
  prepare  read the live image, work out what changed, refuse unsupported changes
  build    build the new image on the private build VM through Azure Run Command
  acr-build  build the same image with ACR Tasks instead, when the build VM is unavailable
  deploy   point the website at the new image
  verify   wait for the new revision, check health and that sign-in is still required
  rollback point the website back at the image recorded by prepare
  assurance-build  build the analyst-assurance image (see build-assurance-image.yml);
                   the assurance job itself is not changed
State passes between steps in release-state.json.
"""
import ast, base64, difflib, gzip, hashlib, json, subprocess, sys, tempfile, time, tomllib, urllib.request, urllib.error
from datetime import datetime, timezone
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
                    'scripts/install_agent_schema.py',
                    # The live IBKR collector ships in its own image on the gateway VM.
                    'Dockerfile.live-collector', 'requirements-live.lock',
                    # Repository metadata, not copied into any image.
                    'LICENSE')
# Lines that put infra/medallion into the platform and agent images (#152). The website image is
# the live website image plus the .py files of src/ibkr_etl, so these have nothing to release here;
# any other change to these files still stops the release.
PLATFORM_ONLY_LINES = {
    '.dockerignore': ('!infra/medallion',),
    'Dockerfile.azure': ('# Medallion data contracts and registers (infra/medallion), read by the layer quality checks',
                         '# and the certified figures. The rest of infra/ stays out of the image.',
                         'COPY infra/medallion ./medallion',
                         'ENV MEDALLION_DIR=/app/medallion'),
}
# Website switches whose code reads tables from these migrations.
SWITCHES_NEEDING_MIGRATIONS = {
    'ONBOARDING_V2_ENABLED': ('064_broker_connections_and_onboarding_cases.sql', '065_onboarding_report_style.sql'),
    'CRISIS_SCENARIOS_ENABLED': ('066_crisis_scenarios.sql',),
    'FACTSHEET_DESIGN_AGENTS_ENABLED': ('067_factsheet_design_agents.sql',),
    # Any page in shadow or on reads the certified figures (see pages below).
    'CERTIFIED_FIGURES_PAGES': ('073_certified_figures.sql',),
}
# Migrations confirmed applied in production (this workflow cannot reach the database).
APPLIED_MIGRATIONS = Path(__file__).resolve().parent / 'applied-migrations.json'
STATE = Path('release-state.json')
ROOT = Path(__file__).resolve().parents[1]


def az(*args, parse=True):
    out = subprocess.run(['az', *args, '-o', 'json'] if parse else ['az', *args], check=True, capture_output=True, text=True).stdout
    return json.loads(out) if parse and out.strip() else out


def git(*args, binary=False):
    return subprocess.run(['git', *args], check=True, capture_output=True, cwd=ROOT, text=not binary).stdout


def package_files(commit):
    files = [f for f in git('ls-tree', '-r', '--name-only', commit, PACKAGE).split() if f.endswith('.py')]
    return {f[len(PACKAGE) + 1:]: git('show', commit + ':' + f, binary=True) for f in files}


def sides(state):
    """The live ("before") and reviewed ("after") src/ibkr_etl files, by name."""
    if state['deployed_commit'] == BASELINE_COMMIT:
        raise SystemExit('The live website was not released from this repo; releases now start from a repo-built image')
    before, after = package_files(state['deployed_commit']), package_files(state['head'])
    merged = 'deploy/live-merged/' + PACKAGE + '/'
    for path in git('ls-tree', '-r', '--name-only', state['deployed_commit'], merged).split():
        if path.endswith('.py'):
            before[path[len(merged):]] = git('show', state['deployed_commit'] + ':' + path, binary=True)
    return before, after


def website_modules(files):
    """The src/ibkr_etl files ibkr_etl.web imports, directly or through other modules.

    Names are paths under src/ibkr_etl, so a subpackage file is
    'factsheet_spec/engine.py'. Importing a subpackage module also imports the
    subpackage's __init__.py.
    """
    def module_files(parts):
        """The files that importing ibkr_etl.<parts> loads (empty if it is not a package module)."""
        found = set()
        for i in range(1, len(parts)):
            found.add('/'.join(parts[:i]) + '/__init__.py')
        if parts:
            found.add('/'.join(parts) + '.py')
            found.add('/'.join(parts) + '/__init__.py')
        return found

    def imported(name, source):
        package = name.split('/')[:-1]
        names = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    if node.level - 1 > len(package):
                        continue
                    base = package[:len(package) - (node.level - 1)]
                elif (node.module or '').split('.')[0] == 'ibkr_etl':
                    base = []
                else:
                    continue
                module = (node.module or '') if node.level else (node.module or '').partition('.')[2]
                parts = base + [m for m in module.split('.') if m]
                names |= module_files(parts)
                # "from .pkg import mod" may name a submodule rather than a value.
                for alias in node.names:
                    if alias.name != '*':
                        names |= module_files(parts + [alias.name])
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith('ibkr_etl.'):
                        names |= module_files(alias.name.split('.')[1:])
        return names
    found, pending = set(), ['web.py']
    while pending:
        name = pending.pop()
        if name in found or name not in files:
            continue
        found.add(name)
        pending.extend(imported(name, files[name]))
    return found


def first_imported(before, after):
    """Unchanged files the reviewed website imports and the live one did not.

    The patch carries only files that changed, so such a file may be missing from
    the live image, or be an older copy nothing there imported (run 25 on 14 Oct:
    the reviewed web.py imports report_design_ui, which the live web.py never did).
    The build adds these files whole.
    """
    return sorted(n for n in website_modules(after) - website_modules(before) if before.get(n) == after[n])


def change_set(state, merged=None):
    """The patch from what is live to what should be live, with before/after hashes.

    Every file goes from the deployed commit to HEAD's src/ibkr_etl. Releases made
    before the repo caught up with the live site shipped merged copies of a few
    files from deploy/live-merged/ (since removed); when the deployed commit has
    those copies, they are what is live, so they are the "before" side. merged
    maps a file to (its exact live copy, that copy merged with the change); see
    merge_live.
    """
    before, after = sides(state)
    for name, (live, result) in (merged or {}).items():
        before[name], after[name] = live, result
    changed = [n for n in set(before) | set(after) if before.get(n) != after.get(n)]
    with tempfile.TemporaryDirectory() as tmp:
        for side, files in (('a', before), ('b', after)):
            (Path(tmp) / side / PACKAGE).mkdir(parents=True)
            for name in changed:
                if name in files:
                    (Path(tmp) / side / PACKAGE / name).parent.mkdir(parents=True, exist_ok=True)
                    (Path(tmp) / side / PACKAGE / name).write_bytes(files[name])
        diff = subprocess.run(['git', 'diff', '--no-index', '--binary', '--no-prefix', 'a', 'b'], cwd=tmp, capture_output=True)
        if diff.returncode not in (0, 1):
            raise SystemExit('Could not compute the change: ' + diff.stderr.decode()[-500:])
    digest = lambda files: {n: hashlib.sha256(d).hexdigest() for n, d in files.items()}
    return diff.stdout, digest(before), digest(after)


def switch_is_on(name, value):
    """A website switch's value turns its code on: 'true', or for the pages setting any page not off."""
    if name == PAGES_SETTING:
        return any(mode != 'off' for mode in page_modes(value).values())
    return str(value or '').strip().lower() == 'true'


def unapplied_migrations(switches, commit):
    """Migrations the switched-on code needs that are not recorded as applied, as on main at commit."""
    needed = sorted({m for name in switches for m in SWITCHES_NEEDING_MIGRATIONS[name]})
    if not needed:
        return []
    recorded = json.loads(APPLIED_MIGRATIONS.read_text())['migrations']
    return [m for m in needed if (recorded.get(m) or {}).get('sha256')
            != hashlib.sha256(git('show', f'{commit}:migrations/postgres/{m}', binary=True)).hexdigest()]


def platform_only_change(path, deployed, head):
    """True when path differs from the deployed commit only by its PLATFORM_ONLY_LINES."""
    lines = PLATFORM_ONLY_LINES.get(path)
    if not lines:
        return False
    try:
        before, after = (git('show', f'{commit}:{path}').splitlines() for commit in (deployed, head))
    except subprocess.CalledProcessError:
        return False
    return [line for line in after if line not in lines] == [line for line in before if line not in lines]


def pyproject_change_is_package_data_only(deployed, head):
    """True when pyproject.toml differs only in [tool.setuptools.package-data].

    That table lists the non-Python files installed with each package. This path ships only the
    .py files of src/ibkr_etl and keeps everything else the live image has, so such a change
    (for example a data file the agent image needs) has nothing to release here. A change to
    dependencies or any other setting still stops the release.
    """

    def settings(commit):
        data = tomllib.loads(git('show', f'{commit}:pyproject.toml'))
        data.get('tool', {}).get('setuptools', {}).pop('package-data', None)
        return data

    return settings(deployed) == settings(head)


def prepare():
    head = git('rev-parse', 'HEAD').strip()
    app = az('containerapp', 'show', '-n', APP, '-g', RG)
    containers = app['properties']['template']['containers']
    if len(containers) != 1:
        raise SystemExit('Expected one website container')
    live = containers[0]['image']
    # A switch that turns on code needing tables the bootstrap job may not have
    # created yet stops the release; with the switch off that code stays dark.
    switched_on = [e['name'] for e in containers[0].get('env') or []
                   if e.get('name') in SWITCHES_NEEDING_MIGRATIONS and switch_is_on(e['name'], e.get('value'))]
    missing = unapplied_migrations(switched_on, head)
    if missing:
        raise SystemExit('Switched on for the website: ' + ', '.join(switched_on) + '. Not recorded as applied '
                         'in deploy/applied-migrations.json (with the sha256 of the file on main): '
                         + ', '.join(missing) + '. Apply them with the bootstrap job and record them, '
                         'or turn the switch off.')
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
    # Database migrations are applied by the bootstrap job, never by this release.
    migrations = [p for p in changed if p.startswith('migrations/postgres/')]
    if migrations:
        print('Not part of the website release (database migrations; apply them with the bootstrap job '
              'before switching the website if the new code needs them):\n  ' + '\n  '.join(migrations))
    packaging = [p for p in changed if p == 'pyproject.toml' and pyproject_change_is_package_data_only(deployed, head)]
    platform_only = [p for p in changed if platform_only_change(p, deployed, head)]
    if platform_only:
        print('Not part of the website release (they only add infra/medallion to the platform image):\n  '
              + '\n  '.join(platform_only))
    if packaging:
        print('Not part of the website release (pyproject.toml lists other package data only; this path '
              'ships the .py files of src/ibkr_etl)')
    image_changes = [p for p in changed if not p.startswith(IGNORED_PREFIXES) and not p.endswith('.md')
                     and p not in skipped and p not in migrations and p not in packaging and p not in platform_only]
    unsupported = [p for p in image_changes if not p.startswith(PACKAGE + '/')]
    if unsupported:
        raise SystemExit('This release path only ships src/ibkr_etl. Other image files changed:\n  ' + '\n  '.join(unsupported))
    data = [p for p in image_changes if p.startswith(PACKAGE + '/') and not p.endswith('.py')]
    if data:
        print('Not part of the website release (package data; this path ships the .py files of src/ibkr_etl, '
              'so the website must not need these):\n  ' + '\n  '.join(data))
    state = {
        'head': head, 'deployed_commit': deployed, 'live_image': live,
        'fqdn': app['properties']['configuration']['ingress']['fqdn'],
        'secrets': secret_kinds(app),
        'target_image': f'{REG}/ibkr-platform:web-{head}',
        'changed_files': [p for p in image_changes],
        'migrations_not_shipped': migrations,
    }
    STATE.write_text(json.dumps(state, indent=2))
    print(json.dumps(state, indent=2))
    if not image_changes:
        print('No website source changes since the live image; nothing to release.')


def merge_live(name, live, base, new):
    """Three-way merge of the change (base -> new) into a live file that drifted from base.

    The live website has hand edits the repo never had. When the change touches
    lines next to one, its patch does not apply exactly, so the build merges the
    change into the exact live copy instead and keeps the live edit. Any
    conflict stops the release; nothing is guessed.
    """
    with tempfile.TemporaryDirectory() as tmp:
        paths = [Path(tmp) / side for side in ('live', 'base', 'new')]
        for path, data in zip(paths, (live, base, new)):
            path.write_bytes(data)
        merged = subprocess.run(['git', 'merge-file', '-p', '-L', 'live', '-L', 'deployed commit', '-L', 'reviewed',
                                 *map(str, paths)], capture_output=True)
    if merged.returncode:
        raise SystemExit(f'The change to {name} conflicts with a live-only edit; nothing built:\n'
                         + merged.stdout.decode(errors='replace')[-3000:])
    return merged.stdout


def kept_live_edits(name, merged, new):
    """What the built file keeps from live that the reviewed commit does not have."""
    return ''.join(difflib.unified_diff(new.decode().splitlines(True), merged.decode().splitlines(True),
                                        'reviewed/' + name, 'built/' + name, n=1))


def capture_files(live, names):
    """Exact copies of named files from a deployed image, read-only, checked against the image's hashes."""
    import io, tarfile
    config = {'capture': names, 'base_image': live, 'target_image': f'{REG}/ibkr-platform:web-capture',
              'source_commit': git('rev-parse', 'HEAD').strip(), 'live_hashes': {}, 'new_hashes': {}, 'patch': ''}
    result, detail = read_only_on_vm(config, 'website-capture')
    files = {}
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(detail)), mode='r:xz') as tar:
        for member in tar.getmembers():
            if member.name not in names or not member.isfile():
                raise SystemExit('Unexpected file in capture: ' + member.name)
            data = tar.extractfile(member).read()
            if hashlib.sha256(data).hexdigest() != result['captured'][member.name]:
                raise SystemExit('Captured file damaged in transfer: ' + member.name)
            files[member.name] = data
    return files


def build():
    state = json.loads(STATE.read_text())
    if not state['changed_files']:
        return
    rejected = overlay_build(state)
    if rejected:
        # Merge the change into exact copies of the files it did not apply to, then build once more.
        before, after = sides(state)
        live = capture_files(state['live_image'], [PACKAGE + '/' + n for n in rejected])
        merged = {}
        for name in rejected:
            copy = live[PACKAGE + '/' + name]
            merged[name] = (copy, merge_live(name, copy, before[name], after[name]))
            print('Merged the change into live ' + name + '; live-only lines kept in the build:\n'
                  + kept_live_edits(name, merged[name][1], after[name]))
        state['merged_with_live'] = sorted(merged)
        STATE.write_text(json.dumps(state, indent=2))
        if overlay_build(state, merged):
            raise SystemExit('Build failed; the website was not changed')


def overlay_build(state, merged=None):
    """Build on the build VM. Returns the files the change did not apply to, for one merge retry."""
    patch, live_hashes, new_hashes = change_set(state, merged)
    before, after = sides(state)
    for name, (copy, result) in (merged or {}).items():
        before[name], after[name] = copy, result
    config = {
        'imported': {n: base64.b64encode(gzip.compress(after[n], 9)).decode() for n in first_imported(before, after)},
        'base_image': state['live_image'], 'target_image': state['target_image'], 'source_commit': state['head'],
        'live_hashes': live_hashes, 'new_hashes': new_hashes,
        'patch': base64.b64encode(gzip.compress(patch, 9)).decode(),
        'website_modules': sorted(website_modules(after)),
    }
    command = remote_script(config)
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
        rejected = []
        if result.get('detail_file'):
            detail = read_detail(base, vm['location'], result['detail_file'], result['detail_size'])
            for name, text in json.loads(gzip.decompress(base64.b64decode(detail))).items():
                print('Live code where the change to ' + name + ' does not apply:\n' + text)
                rejected.append(name)
        if not result.get('passed') or view.get('exitCode') != 0:
            if merged is None and rejected and result.get('error', '').startswith('Some of the change does not apply'):
                return sorted(rejected)
            raise SystemExit('Build failed; the website was not changed')
        state['pushed_image'] = result['pushed_image']
        STATE.write_text(json.dumps(state, indent=2))
        return []
    finally:
        if started:
            print('Stopping the build VM again')
            subprocess.run(['az', 'vm', 'deallocate', '-g', RG, '-n', VM, '--no-wait'], capture_output=True)


# What the VM build read from the live image (run 25, 14 Oct). ACR Tasks cannot inspect the image
# first, so the in-image step checks the owner of /app/src against RUNTIME_USER and stops if it differs.
LIVE_PYTHON = '/usr/local/bin/python3'
RUNTIME_USER = '10001:10001'


def acr_context(state, folder):
    """Write the ACR Tasks build context: Dockerfile, the in-image swap script and the reviewed files."""
    before, after = sides(state)
    changed = sorted(n for n in set(before) | set(after) if before.get(n) != after.get(n))
    digest = lambda data: None if data is None else hashlib.sha256(data).hexdigest()
    imported = first_imported(before, after)
    release = {'runtime_user': RUNTIME_USER,
               'touched': {n: {'live': digest(before.get(n)), 'new': digest(after.get(n))} for n in changed},
               'imported': {n: digest(after[n]) for n in imported}}
    stage = folder / 'release'
    for name in changed + imported:
        if name in after:
            (stage / 'files' / name).parent.mkdir(parents=True, exist_ok=True)
            (stage / 'files' / name).write_bytes(after[name])
    stage.mkdir(parents=True, exist_ok=True)
    (stage / 'release.json').write_text(json.dumps(release, indent=1, sort_keys=True))
    (stage / 'apply.py').write_text((ROOT / 'deploy' / 'website_acr_apply.py').read_text())
    install = remote_module().INSTALL_SCRIPT.replace('REMOVED', repr([n for n in changed if n not in after])) \
                                            .replace('RUNTIME_USER', repr(RUNTIME_USER))
    (stage / 'install.py').write_text(install)
    (folder / 'Dockerfile').write_text('\n'.join([
        'FROM ' + state['live_image'], 'USER 0:0',
        'LABEL ai.wealthryse.source-commit="' + state['head'] + '"',
        'COPY release/ /tmp/wealthryse-release/',
        'RUN ' + json.dumps([LIVE_PYTHON, '/tmp/wealthryse-release/apply.py']),
        'RUN ' + json.dumps([LIVE_PYTHON, '/tmp/wealthryse-release/install.py']),
        'RUN ' + json.dumps([LIVE_PYTHON, '-c', "import shutil; shutil.rmtree('/tmp/wealthryse-release')"]),
        'USER ' + RUNTIME_USER,
        'RUN ' + json.dumps([LIVE_PYTHON, '-c', 'import ibkr_etl.web']),
        'RUN ' + json.dumps([LIVE_PYTHON, 'scripts/check_report_runtime.py'])]) + '\n')
    return release


def remote_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location('website_overlay_build_remote', ROOT / 'deploy' / 'website_overlay_build_remote.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def acr_build():
    """Build the release image with ACR Tasks, inside Azure, from the live image; no build VM involved."""
    state = json.loads(STATE.read_text())
    if not state['changed_files']:
        return
    registry = REG.split('.')[0]
    tag = state['target_image'][len(REG) + 1:]
    with tempfile.TemporaryDirectory() as tmp:
        release = acr_context(state, Path(tmp))
        print(json.dumps({'touched': sorted(release['touched']), 'first_imported': sorted(release['imported'])}, indent=2))
        built = subprocess.run(['az', 'acr', 'build', '-r', registry, '-t', tag, '--platform', 'linux/amd64',
                                '-f', str(Path(tmp) / 'Dockerfile'), tmp])
    if built.returncode:
        raise SystemExit('Build failed; the website was not changed')
    runs = az('acr', 'task', 'list-runs', '-r', registry, '--top', '20')
    digests = {image['digest'] for run in runs if run.get('status') == 'Succeeded'
               for image in run.get('outputImages') or [] if f"{image.get('repository')}:{image.get('tag')}" == tag}
    if len(digests) != 1:
        raise SystemExit('Could not find the one pushed digest for ' + tag)
    state['pushed_image'] = IMAGE_PREFIX + digests.pop()[len('sha256:'):]
    STATE.write_text(json.dumps(state, indent=2))
    print('Built', state['pushed_image'])


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

    With no --image, the files come from the live website image. They go to
    deploy/image-captures/<image>/ under their repository paths; a bare name
    means src/ibkr_etl/<name>.
    """
    image, names = image_argument(sys.argv[2:])
    names = [n if '/' in n else PACKAGE + '/' + n for n in names]
    if not names or any(not n.endswith('.py') or '..' in n or not n.startswith(('src/ibkr_etl/', 'scripts/')) for n in names):
        raise SystemExit('Name src/ibkr_etl/... or scripts/... .py files to copy')
    live = image or az('containerapp', 'show', '-n', APP, '-g', RG)['properties']['template']['containers'][0]['image']
    folder = live.split('@sha256:')[1][:12] if '@sha256:' in live else live.rsplit(':', 1)[-1][:16]
    target = ROOT / 'deploy' / 'image-captures' / folder
    for name, data in capture_files(live, names).items():
        (target / name).parent.mkdir(parents=True, exist_ok=True)
        (target / name).write_bytes(data)
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


def secret_kinds(app):
    """{secret name: 'key-vault' or 'inline'} for the website; names and kinds only, never a value. Every step here
    changes only the image or named settings, so this must be the same before and after (the network thread moves
    secrets to Key Vault references, and an older inline value coming back would break sign-in or the broker)."""
    secrets = (app.get('properties') or {}).get('configuration', {}).get('secrets') or []
    return {s['name']: 'key-vault' if s.get('keyVaultUrl') else 'inline' for s in secrets if s.get('name')}


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
    secrets = secret_kinds({'properties': app})
    report = {'revision': revision, 'health': rev.get('healthState'), 'running': rev.get('runningState'), 'checks': checks,
              'secrets': secrets}
    print(json.dumps(report, indent=2))
    if 'secrets' in state and secrets != state['secrets']:
        raise SystemExit('The website secrets are not as they were before this run (names and kinds above, before: '
                         + json.dumps(state['secrets'], sort_keys=True) + '). This step never changes secrets: tell '
                         'the network thread before anything else changes the website.')
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
                                 'secrets': secret_kinds(app),
                                 'previous_mode': env.get('AGENT_UI_MODE', 'off')}, indent=2))
    print('Website chat set to', mode)


# Website pages on the certified figures (docs/MEDALLION_CERTIFIED_FIGURES.md, "How a page moves").
PAGES_SETTING = 'CERTIFIED_FIGURES_PAGES'
PAGES_REGISTER = ROOT / 'infra' / 'medallion' / 'certified-figures.json'
# A website built from a commit with this file can read the register (the release ships only .py files).
PAGES_READERS = 'src/ibkr_etl/certified_figures_register_data.py'
# ... and with this one it can show the charts on them (batch 5, page key charts).
CHARTS_READERS = 'src/ibkr_etl/certified_charts.py'
PAGES_STAGES = ('off', 'shadow', 'batch-1', 'batch-2', 'batch-3', 'batch-4', 'batch-5')
PAGES_QUIET = ((7, 50), (9, 25))   # UTC: no changes while the daily runs publish
PAGE_RANK = {'off': 0, 'shadow': 1, 'on': 2}


def register_pages():
    """The register's pages in move order: {page: batch}."""
    pages = json.loads(PAGES_REGISTER.read_text(encoding='utf-8'))['pages']
    return {page: item['batch'] for page, item in sorted(pages.items(), key=lambda kv: kv[1]['order'])}


def page_modes(value):
    """{page: off|shadow|on} from a CERTIFIED_FIGURES_PAGES value, read as ibkr_etl.certified_figures does."""
    modes = {page: 'off' for page in register_pages()}
    for part in str(value or '').split(','):
        name, _, mode = part.partition('=')
        name, mode = name.strip().lower(), mode.strip().lower()
        if name in modes and mode in PAGE_RANK:
            modes[name] = mode
    return modes


def pages_value(stage):
    """The setting for a stage: off; every page in shadow; or batches 1 to N on and the rest in shadow."""
    if stage == 'off':
        return 'off'
    through = 0 if stage == 'shadow' else int(stage.rsplit('-', 1)[1])
    return ','.join(page + '=' + ('on' if batch <= through else 'shadow') for page, batch in register_pages().items())


def pages_blocked(now, before, after):
    """Why the pages may not change now, or None: never while the daily runs publish, and from 1 to
    7 Nov (month-end) only towards today's figures."""
    (h1, m1), (h2, m2) = PAGES_QUIET
    if (h1, m1) <= (now.hour, now.minute) < (h2, m2):
        return 'the daily runs publish between 07:50 and 09:25 UTC'
    if now.month == 11 and now.day <= 7:
        old, new = page_modes(before), page_modes(after)
        raised = [page for page in new if PAGE_RANK[new[page]] > PAGE_RANK[old[page]]]
        if raised:
            return 'month-end (1 to 7 Nov) allows only moving pages back, not ' + ', '.join(raised)
    return None


def pages():
    """Set CERTIFIED_FIGURES_PAGES on the website for a stage; leaves the image alone."""
    stage = sys.argv[2]
    if stage not in PAGES_STAGES:
        raise SystemExit('Stage must be one of ' + ', '.join(PAGES_STAGES))
    app = az('containerapp', 'show', '-n', APP, '-g', RG)
    container = app['properties']['template']['containers'][0]
    env = {e['name']: e.get('value') for e in container.get('env') or []}
    before, after = env.get(PAGES_SETTING) or 'off', pages_value(stage)
    print(PAGES_SETTING, 'was', repr(before), '; for', stage, ':', repr(after))
    reason = pages_blocked(datetime.now(timezone.utc), before, after)
    if reason:
        raise SystemExit('Not now: ' + reason + '. Nothing was changed.')
    if switch_is_on(PAGES_SETTING, after):
        tag = container['image'].split(':')[-1] if '@' not in container['image'] else ''
        deployed = tag[len('web-'):] if tag.startswith('web-') else ''
        needed = [PAGES_READERS] + ([CHARTS_READERS] if page_modes(after).get('charts') == 'on' else [])
        if not deployed or any(subprocess.run(['git', 'cat-file', '-e', deployed + ':' + path], cwd=ROOT,
                                              capture_output=True).returncode for path in needed):
            raise SystemExit('The live website (' + container['image'].split('/')[-1] + ') cannot read the certified '
                             'figures yet: run Deploy website from main first. Nothing was changed.')
        missing = unapplied_migrations([PAGES_SETTING], git('rev-parse', 'HEAD').strip())
        if missing:
            raise SystemExit('Not recorded as applied in deploy/applied-migrations.json: ' + ', '.join(missing)
                             + '. Nothing was changed.')
    if page_modes(before) == page_modes(after):
        STATE.write_text(json.dumps({'changed_files': []}, indent=2))
        print('The pages are already like that; nothing to change.')
        return
    suffix = 'pages-' + stage + '-' + str(int(time.time()))[-6:]
    az('containerapp', 'update', '-n', APP, '-g', RG, '--container-name', 'web', '--revision-suffix', suffix,
       '--set-env-vars', PAGES_SETTING + '=' + after, parse=False)
    STATE.write_text(json.dumps({'changed_files': [PAGES_SETTING], 'revision': APP + '--' + suffix,
                                 'fqdn': app['properties']['configuration']['ingress']['fqdn'],
                                 'secrets': secret_kinds(app),
                                 'previous_pages': before}, indent=2))
    print('Website pages set for', stage)


def rollback():
    state = json.loads(STATE.read_text())
    if 'previous_pages' in state:
        suffix = 'pages-rollback-' + str(int(time.time()))[-6:]
        az('containerapp', 'update', '-n', APP, '-g', RG, '--container-name', 'web', '--revision-suffix', suffix,
           '--set-env-vars', PAGES_SETTING + '=' + state['previous_pages'], parse=False)
        print('Website pages set back to', state['previous_pages'])
        return
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
    {'prepare': prepare, 'build': build, 'acr-build': acr_build, 'capture': capture, 'deploy': deploy, 'verify': verify, 'rollback': rollback, 'chat': chat, 'pages': pages,
     'assurance-build': assurance_build, 'inventory': inventory}[sys.argv[1]]()
