"""Build a website image on the private build VM. Run only via Azure Run Command.

With CONFIG['replacements'] it instead builds the analyst-assurance image: the
exact image the assurance job runs, with only the named strings replaced (each
must appear exactly once where expected) and a check of the result.

The deploy-website workflow fills in CONFIG and runs this as root inside a
private mount namespace (`unshare --mount`). It starts from the exact image
the website runs today, applies only the reviewed change to src/ibkr_etl on top
of the live files (every hunk must apply exactly), builds
with no network, and pushes one new tag over the registry's private endpoint.
No application starts and no database is touched.
"""
import base64, gzip, hashlib, io, json, os, re, shutil, tarfile, socket, ssl, subprocess, tempfile, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

CONFIG = None  # replaced by the workflow
PREFIX = 'WEBSITE_BUILD_RESULT '
HOSTS = {'ibkrv2stgauhecwc4itaoo.azurecr.io': '10.50.10.5', 'ibkrv2stgauhecwc4itaoo.eastasia.data.azurecr.io': '10.50.10.4'}
REG = 'ibkrv2stgauhecwc4itaoo.azurecr.io'


def run(args, operation, *, timeout=900, input=None, env=None, cwd=None):
    r = subprocess.run(args, input=input, capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd)
    if r.returncode:
        # Both streams: docker build writes warnings to stderr but a failing
        # step's own output (such as an import traceback) to stdout.
        output = '\n'.join(part for part in (r.stderr.strip(), r.stdout.strip()) if part)
        raise RuntimeError(operation + ' failed with exit ' + str(r.returncode) + ': ' + output[-1500:])
    return r.stdout


def request(url, data=None, headers=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    body = None if data is None else urllib.parse.urlencode(data).encode()
    with opener.open(urllib.request.Request(url, data=body, headers=headers or {}), timeout=30) as r:
        return json.load(r)


# Runs inside the new image as root: mirror the source into the installed package
# if it lives elsewhere, drop removed files and caches, and hand /app/src back to
# the image's runtime user.
INSTALL_SCRIPT = """
import importlib.util, os, pwd, grp, shutil
src = '/app/src/ibkr_etl'
removed = REMOVED
site = os.path.dirname(importlib.util.find_spec('ibkr_etl').origin)
for name in removed:
    if os.path.exists(os.path.join(src, name)):
        os.remove(os.path.join(src, name))
if os.path.realpath(site) != os.path.realpath(src):
    shutil.copytree(src, site, dirs_exist_ok=True)
    for name in removed:
        if os.path.exists(os.path.join(site, name)):
            os.remove(os.path.join(site, name))
for top in {src, site}:
    for root, dirs, files in os.walk(top):
        if '__pycache__' in dirs:
            shutil.rmtree(os.path.join(root, '__pycache__'))
            dirs.remove('__pycache__')
        os.chmod(root, 0o755)
        for f in files:
            os.chmod(os.path.join(root, f), 0o644)
spec = RUNTIME_USER
user, _, group = spec.partition(':')
uid = int(user) if user.isdigit() else pwd.getpwnam(user).pw_uid
gid = int(group) if group.isdigit() else (grp.getgrnam(group).gr_gid if group else (pwd.getpwuid(uid).pw_gid if not user.isdigit() else uid))
for root, dirs, files in os.walk('/app/src'):
    for name in [root] + [os.path.join(root, f) for f in dirs + files]:
        os.chown(name, uid, gid)
"""


def find_python(client, base, env):
    """Absolute path of the live image's Python, read from the image's files without running it."""
    config = json.loads(run(client + ['image', 'inspect', base, '--format', '{{json .Config}}'], 'live image config', env=env))
    path = next((e.split('=', 1)[1] for e in config.get('Env') or [] if e.startswith('PATH=')), '')
    container = run(client + ['create', base], 'inspect container', env=env).strip()
    entries = {}
    try:
        export = subprocess.Popen(client + ['export', container], stdout=subprocess.PIPE, env=env)
        with tarfile.open(fileobj=export.stdout, mode='r|') as archive:
            for member in archive:
                name = '/' + member.name.lstrip('./')
                if os.path.dirname(name).endswith('/bin') and (member.isfile() or member.issym()):
                    entries[name] = member.linkname if member.issym() else None
        export.wait(timeout=600)
    finally:
        subprocess.run(client + ['rm', '-f', container], capture_output=True, timeout=120, env=env)

    def resolve(name, depth=0):
        if name not in entries or depth > 8:
            return None
        link = entries[name]
        if link is None:
            return name
        return resolve(os.path.normpath(os.path.join(os.path.dirname(name), link)), depth + 1)

    dirs = [d for d in path.split(':') if d] + ['/usr/local/bin', '/usr/bin', '/opt/venv/bin', '/app/.venv/bin', '/venv/bin']
    names = ['python3', 'python'] + sorted({os.path.basename(n) for n in entries if re.fullmatch(r'python3\.\d+', os.path.basename(n))}, reverse=True)
    for folder in dict.fromkeys(dirs):
        for name in names:
            if resolve(folder + '/' + name):
                return folder + '/' + name
    raise ValueError('No Python found in the live image: ' + json.dumps({
        'found': sorted(n for n in entries if 'python' in os.path.basename(n))[:20], 'path': path}))


def apply_replacements(package, replacements):
    """Replace each old string exactly once; refuse anything ambiguous or missing.

    Each entry is {'file': name or None, 'old': text, 'new': text}. With a file
    the old text must appear exactly once in that file; without one, exactly
    once across the whole package. Returns {file: count} and any other files
    that also mention an old string (reported, never changed).
    """
    files = {p.relative_to(package).as_posix(): p for p in sorted(package.rglob('*.py')) if '__pycache__' not in p.parts}
    texts = {name: path.read_text() for name, path in files.items()}
    touched, elsewhere = {}, {}
    for item in replacements:
        old, new, wanted = item['old'], item['new'], item.get('file')
        found = {name: text.count(old) for name, text in texts.items() if old in text}
        if wanted is not None:
            if found.get(wanted) != 1:
                raise ValueError(old + ' appears ' + str(found.get(wanted, 0)) + ' times in ' + wanted + '; nothing built')
            name = wanted
        else:
            if sum(found.values()) != 1:
                raise ValueError(old + ' appears ' + str(sum(found.values())) + ' times in the package; nothing built')
            name = next(iter(found))
        texts[name] = texts[name].replace(old, new)
        touched[name] = touched.get(name, 0) + 1
        others = sorted(n for n in found if n != name)
        if others:
            elsewhere[old] = others
    for name in touched:
        files[name].write_text(texts[name])
    return touched, elsewhere


DESIGN_CHECK = ("from ibkr_etl.factsheet_spec import spec\n"
                "ids = IDS\n"
                "have = spec.built_in_designs()\n"
                "assert set(ids) <= set(have), ('designs missing', sorted(set(ids) - set(have)))\n"
                "for i in ids:\n"
                "    spec.load_design(i)\n")


class Captured(Exception):
    """Finished early (a capture or the assurance build); skip the website build."""


def tree_hashes(folder):
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob('*.py')) if '__pycache__' not in p.parts}


def without_files(patch, names):
    """The patch minus the sections for the named src/ibkr_etl files."""
    keep, out = True, []
    for line in patch.splitlines(keepends=True):
        if line.startswith(b'diff --git '):
            keep = line.split()[-1].decode().partition('src/ibkr_etl/')[2] not in names
        if keep:
            out.append(line)
    return b''.join(out)


def rejected_context(package):
    """For each hunk patch rejected, the live lines it was aimed at."""
    out = {}
    for rej in sorted(package.rglob('*.rej')):
        name = rej.relative_to(package).as_posix()[:-len('.rej')]
        lines = (package / name).read_text(errors='replace').splitlines()
        parts = []
        for header in re.findall(r'^@@ -(\d+)(?:,(\d+))? ', rej.read_text(errors='replace'), re.M):
            start, count = int(header[0]), int(header[1] or 1)
            lo, hi = max(start - 25, 1), min(start + count + 25, len(lines))
            parts.append('--- live lines %d-%d\n' % (lo, hi) + '\n'.join(lines[lo - 1:hi]))
        out[name] = '\n'.join(parts)
    return out

def main():
    config = CONFIG
    base, target = config['base_image'], config['target_image']
    prefix = '/ibkr-platform:assurance-' if config.get('replacements') else '/ibkr-platform:web-'
    if not base.startswith(REG + '/') or not target.startswith(REG + prefix):
        raise ValueError('Unexpected image names')
    os.umask(0o077)
    if os.geteuid() != 0 or os.readlink('/proc/self/ns/mnt') == os.readlink('/proc/1/ns/mnt'):
        raise ValueError('A separate root mount namespace is mandatory')
    for tool in ('dockerd', 'containerd', 'docker', 'mount', 'umount', 'patch'):
        if not shutil.which(tool):
            raise ValueError('Required build tool missing: ' + tool)
    if shutil.disk_usage('/tmp').free < 12 * 1024 ** 3:
        raise ValueError('Insufficient disposable build space')
    root = Path(tempfile.mkdtemp(prefix='wealthryse-website-build-')).resolve()
    original = Path('/etc/hosts').read_bytes()
    hosts = root / 'hosts'
    hosts.write_bytes(original + b'\n' + ''.join(ip + ' ' + host + '\n' for host, ip in HOSTS.items()).encode())
    env = {k: v for k, v in os.environ.items() if k.upper() not in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_CONFIG')}
    env['DOCKER_CONFIG'] = str(root / 'credentials')
    Path(env['DOCKER_CONFIG']).mkdir(mode=0o700)
    process = log = containerd = None
    detail = ''
    mounted = False
    result = {'base_image': base, 'target_image': target, 'source_commit': config['source_commit']}
    try:
        run(['mount', '--bind', str(hosts), '/etc/hosts'], 'namespace-only hosts bind', timeout=15)
        mounted = True
        for host, expected in HOSTS.items():
            with socket.create_connection((host, 443), timeout=15) as sock:
                with ssl.create_default_context().wrap_socket(sock, server_hostname=host) as secure:
                    if secure.getpeername()[0] != expected:
                        raise ValueError('Registry is not reached over its private address')
        token = request('http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https%3A%2F%2Fmanagement.azure.com%2F', headers={'Metadata': 'true'})['access_token']
        payload = token.split('.')[1]
        tenant = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))['tid']
        refresh = request('https://' + REG + '/oauth2/exchange', {'grant_type': 'access_token', 'service': REG, 'tenant': tenant, 'access_token': token})['refresh_token']
        del token
        location = root / 'daemon'
        location.mkdir()
        log = (location / 'daemon.log').open('w')
        sock = str(location / 'docker.sock')
        # The VM's own containerd lives in the host mount namespace, where this
        # namespace's mounts are invisible, so containers it started saw an
        # empty filesystem. Run a private containerd inside this namespace.
        containerd_sock = str(location / 'containerd.sock')
        (location / 'containerd.toml').write_text(
            'version = 2\n'
            'root = "' + str(location / 'containerd-root') + '"\n'
            'state = "' + str(location / 'containerd-state') + '"\n'
            'disabled_plugins = ["io.containerd.grpc.v1.cri"]\n'
            '[grpc]\n  address = "' + containerd_sock + '"\n'
            '[ttrpc]\n  address = "' + containerd_sock + '.ttrpc"\n')
        # The build script's own umask (077) must not reach the daemons: layers
        # unpacked under it leave parent folders like /usr/local unreadable to
        # the image's non-root user.
        def daemon_umask():
            os.umask(0o022)
        containerd = subprocess.Popen(['containerd', '--config', str(location / 'containerd.toml')], stdout=log, stderr=log, env=env,
                                      preexec_fn=daemon_umask)
        for _ in range(60):
            if containerd.poll() is not None:
                raise RuntimeError('Isolated containerd exited')
            if os.path.exists(containerd_sock):
                break
            time.sleep(1)
        else:
            raise RuntimeError('Isolated containerd readiness timeout')
        process = subprocess.Popen(['dockerd', '--host=unix://' + sock, '--containerd=' + containerd_sock,
                                    '--data-root=' + str(location / 'data'), '--exec-root=' + str(location / 'exec'),
                                    '--pidfile=' + str(location / 'pid'), '--bridge=none', '--iptables=false', '--ip6tables=false'],
                                   stdout=log, stderr=log, env=env, preexec_fn=daemon_umask)
        client = ['docker', '--host', 'unix://' + sock]
        for _ in range(60):
            if process.poll() is not None:
                raise RuntimeError('Isolated Docker daemon exited')
            if subprocess.run(client + ['info'], capture_output=True, env=env, timeout=5).returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError('Isolated Docker daemon readiness timeout')
        run(client + ['login', REG, '--username', '00000000-0000-0000-0000-000000000000', '--password-stdin'], 'registry sign-in', input=refresh, timeout=60, env=env)
        del refresh
        run(client + ['pull', base], 'live image pull', env=env)

        context = root / 'context'
        (context / 'src').mkdir(parents=True)
        container = run(client + ['create', base], 'live image unpack', env=env).strip()
        try:
            run(client + ['cp', container + ':/app/src/ibkr_etl', str(context / 'src')], 'live source copy', env=env)
        finally:
            run(client + ['rm', container], 'live image cleanup', env=env)
        package = context / 'src' / 'ibkr_etl'
        live = tree_hashes(package)
        if config.get('inventory') or config.get('capture'):
            # Read-only. Repo-style paths (src/ibkr_etl/..., scripts/...) cover the
            # job entry points too; a bare name means src/ibkr_etl/<name>.
            container = run(client + ['create', base], 'image unpack', env=env).strip()
            try:
                copied = subprocess.run(client + ['cp', container + ':/app/scripts', str(context)],
                                        capture_output=True, text=True, timeout=600, env=env)
            finally:
                run(client + ['rm', container], 'image cleanup', env=env)
            roots = {'src/ibkr_etl/': package}
            if copied.returncode == 0:
                roots['scripts/'] = context / 'scripts'
            files = {prefix + name: folder / name for prefix, folder in roots.items() for name in tree_hashes(folder)}
            hashes = {prefix + name: digest for prefix, folder in roots.items() for name, digest in tree_hashes(folder).items()}
        if config.get('inventory'):
            # Compared here, so the answer fits in Run Command's 4 KB of output: the
            # image's file names plus which of them differ from the repository.
            repo = json.loads(gzip.decompress(base64.b64decode(config['repo_hashes'])))
            names = sorted(hashes)
            differ = [i for i, n in enumerate(names) if repo.get(n) != hashes[n][:16]]
            listing = base64.b64encode(gzip.compress(json.dumps({'files': names, 'differ': differ}).encode(), 9)).decode()
            result.update(passed=True, files=len(hashes), has_scripts='scripts/' in roots)
            if len(listing) <= 3000:
                result.update(listing=listing)
            else:
                detail = listing
            raise Captured()
        if config.get('capture'):
            # Read-only: hand back exact copies of the named live files; nothing is built.
            names = config['capture']
            paths = {n: (n if '/' in n else 'src/ibkr_etl/' + n) for n in names}
            missing = [n for n, path in paths.items() if path not in files]
            if missing:
                raise ValueError('Not in the live image: ' + ', '.join(missing))
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode='w:xz', preset=9) as tar:
                for name in names:
                    tar.add(str(files[paths[name]]), arcname=name)
            detail = base64.b64encode(buffer.getvalue()).decode()
            result.update(passed=True, captured={n: hashes[paths[n]] for n in names})
            raise Captured()
        if config.get('replacements'):
            touched, elsewhere = apply_replacements(package, config['replacements'])
            changed = sorted(n for n, h in tree_hashes(package).items() if live.get(n) != h)
            if changed != sorted(touched):
                raise ValueError('The replacements changed files they should not have; nothing built')
            result.update(touched_files=touched, also_mentioned_in=elsewhere)
            user = run(client + ['image', 'inspect', base, '--format', '{{.Config.User}}'], 'image user', env=env).strip() or '0'
            python = find_python(client, base, env)
            install = (INSTALL_SCRIPT.replace('REMOVED', repr([])).replace('RUNTIME_USER', repr(user)))
            lines = ['FROM ' + base, 'USER 0:0',
                     'LABEL ai.wealthryse.source-commit="' + config['source_commit'] + '"',
                     'COPY src/ibkr_etl/ /app/src/ibkr_etl/',
                     'RUN ' + json.dumps([python, '-c', install]),
                     'USER ' + user,
                     'RUN ' + json.dumps([python, '-c', config['check']])]
            result.update(runtime_user=user, python=python)
            (context / 'Dockerfile').write_text('\n'.join(lines) + '\n')
            run(client + ['build', '--network=none', '--pull=false', '-t', target, str(context)], 'assurance image build', timeout=1800, env=env)
            run(client + ['push', target], 'assurance image push', env=env)
            digests = json.loads(run(client + ['image', 'inspect', target, '--format', '{{json .RepoDigests}}'], 'pushed digest', env=env))
            pushed = [d for d in digests if d.startswith(REG + '/ibkr-platform@sha256:')]
            if len(pushed) != 1:
                raise ValueError('Pushed digest missing')
            result.update(passed=True, pushed_image=pushed[0])
            raise Captured()
        expected, wanted = config['live_hashes'], config['new_hashes']
        # Counts only: the full lists were reported by earlier runs and the
        # Run Command output is small.
        result['live_drift'] = {
            'only_live': len(set(live) - set(expected)),
            'only_expected': len(set(expected) - set(live)),
            'different': len([n for n in set(live) & set(expected) if live[n] != expected[n]]),
        }
        # The live source is not exactly the commit the change was made on, so the
        # change is applied on top of whatever the live files are. Each file the
        # change touches must be where the change expects it, and every hunk must
        # apply exactly (no fuzz); otherwise nothing is built.
        touched = sorted(n for n in set(expected) | set(wanted) if expected.get(n) != wanted.get(n))
        # A file already exactly as the reviewed commit has it (for example code
        # the live site had before the repo caught up) is left alone.
        already = [n for n in touched if n in live and live[n] == wanted.get(n)]
        result['already_live'] = already
        # A changed file the website does not import and the live image never had
        # (report_design_worker.py, run 30 on 14 Oct) belongs to other jobs, so it
        # is left out of the website build rather than stopping the release.
        modules = config.get('website_modules')
        skipped = [n for n in touched if modules is not None and n not in modules and n not in live]
        result['not_website_files'] = skipped
        touched = [n for n in touched if n not in skipped]
        for name in touched:
            if name in already:
                continue
            if (name in expected) != (name in live):
                raise ValueError('Live website file ' + name + ' is not where the change expects it; nothing built')
        # Some live files were saved with Windows line endings; the change uses
        # Unix ones. Python reads both, so touched files are normalised first.
        normalised = []
        for name in touched:
            path = package / name
            if path.exists() and b'\r\n' in path.read_bytes():
                path.write_bytes(path.read_bytes().replace(b'\r\n', b'\n'))
                normalised.append(name)
        result['line_endings_normalised'] = normalised
        live = {**live, **{n: h for n, h in tree_hashes(package).items() if n in normalised}}
        patch_file = root / 'change.patch'
        patch_file.write_bytes(without_files(gzip.decompress(base64.b64decode(config['patch'])), already + skipped))
        applied = subprocess.run(['patch', '-p1', '--forward', '--fuzz=0', '--no-backup-if-mismatch', '-i', str(patch_file)],
                                 capture_output=True, text=True, timeout=300, cwd=str(context))
        if applied.returncode:
            detail = base64.b64encode(gzip.compress(json.dumps(rejected_context(package)).encode(), 9)).decode()
            raise ValueError('Some of the change does not apply to the live files; nothing built')
        patched = tree_hashes(package)
        for name in touched:
            if live.get(name) == expected.get(name) and patched.get(name) != wanted.get(name):
                raise ValueError('Patched ' + name + ' does not match the reviewed commit; nothing built')
        if any(patched.get(n) != live[n] for n in live if n not in touched):
            raise ValueError('The change altered files it should not have; nothing built')
        # Files the reviewed website imports for the first time that did not change in
        # the repo are not in the patch; the live image may lack them or hold a copy
        # nothing imported, so they are added whole, exactly as reviewed.
        first = []
        for name, data in sorted((config.get('imported') or {}).items()):
            content = gzip.decompress(base64.b64decode(data))
            if name.startswith('/') or '..' in name.split('/') or not name.endswith('.py') or name in touched or hashlib.sha256(content).hexdigest() != wanted.get(name):
                raise ValueError('First-imported file ' + name + ' does not match the reviewed commit; nothing built')
            if patched.get(name) != wanted[name]:
                (package / name).parent.mkdir(parents=True, exist_ok=True)
                (package / name).write_bytes(content)
                first.append(name)
        result['first_imported'] = first
        # Built-in factsheet designs (data files the designer reads) are written whole,
        # each checked against the reviewed commit's hash.
        design_hashes, designs = config.get('design_hashes') or {}, []
        for name, data in sorted((config.get('designs') or {}).items()):
            content = gzip.decompress(base64.b64decode(data))
            if not re.fullmatch(r'factsheet_spec/designs/[A-Za-z0-9_.-]+\.json', name) \
                    or hashlib.sha256(content).hexdigest() != design_hashes.get(name):
                raise ValueError('Design file ' + name + ' does not match the reviewed commit; nothing built')
            if not (package / name).is_file() or (package / name).read_bytes() != content:
                (package / name).parent.mkdir(parents=True, exist_ok=True)
                (package / name).write_bytes(content)
                designs.append(name)
        result['designs_written'] = designs
        result['touched_files'] = touched
        removed = sorted(n for n in set(expected) - set(wanted) if n in live)
        # The live image has no "root" passwd entry and may have no shell, so
        # the build uses numeric ids and exec-form Python steps only, and runs
        # the checks as the image's own user.
        user = run(client + ['image', 'inspect', base, '--format', '{{.Config.User}}'], 'live image user', env=env).strip() or '0'
        python = find_python(client, base, env)
        install = (INSTALL_SCRIPT.replace('REMOVED', repr(removed)).replace('RUNTIME_USER', repr(user)))
        lines = ['FROM ' + base, 'USER 0:0',
                 'LABEL ai.wealthryse.source-commit="' + config['source_commit'] + '"',
                 'COPY src/ibkr_etl/ /app/src/ibkr_etl/',
                 'RUN ' + json.dumps([python, '-c', install]),
                 'USER ' + user,
                 'RUN ' + json.dumps([python, '-c', 'import ibkr_etl.web']),
                 'RUN ' + json.dumps([python, 'scripts/check_report_runtime.py'])]
        if design_hashes:
            # The installed website lists and loads every reviewed design.
            ids = sorted(n[len('factsheet_spec/designs/'):-len('.json')] for n in design_hashes)
            lines.append('RUN ' + json.dumps([python, '-c', DESIGN_CHECK.replace('IDS', repr(ids))]))
        result.update(runtime_user=user, python=python)
        (context / 'Dockerfile').write_text('\n'.join(lines) + '\n')
        run(client + ['build', '--network=none', '--pull=false', '-t', target, str(context)], 'website image build', timeout=1800, env=env)
        run(client + ['push', target], 'website image push', env=env)
        digests = json.loads(run(client + ['image', 'inspect', target, '--format', '{{json .RepoDigests}}'], 'pushed digest', env=env))
        pushed = [d for d in digests if d.startswith(REG + '/ibkr-platform@sha256:')]
        if len(pushed) != 1:
            raise ValueError('Pushed digest missing')
        result.update(passed=True, pushed_image=pushed[0], removed_files=removed)
    except Captured:
        pass
    except Exception as error:
        result.update(passed=False, error=str(error)[-1500:])
        if log is not None:
            log.flush()
            result['daemon_log_tail'] = (location / 'daemon.log').read_text(errors='replace')[-600:]
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=45)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        if containerd is not None and containerd.poll() is None:
            containerd.terminate()
            try:
                containerd.wait(timeout=45)
            except subprocess.TimeoutExpired:
                containerd.kill()
                containerd.wait(timeout=10)
        if log is not None:
            log.close()
        if mounted:
            subprocess.run(['umount', '/etc/hosts'], timeout=15)
        if root.parent == Path('/tmp') and root.name.startswith('wealthryse-website-build-'):
            shutil.rmtree(root, ignore_errors=True)
    # Run Command returns only 4 KB of output, so larger detail (conflicts or
    # captured files) is left in a root-only file the workflow reads in pieces
    # and then removes.
    if detail:
        path = '/var/tmp/wealthryse-build-detail-' + config['source_commit'][:12] + '.txt'
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(detail)
        result.update(detail_file=path, detail_size=len(detail))
    print(PREFIX + json.dumps(result), flush=True)
    if not result.get('passed'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
