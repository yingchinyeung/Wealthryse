"""Freeze a reporting-only candidate from verified installed-image source.

Local preparation only. Never follows archive paths or extracts credentials.
Only explicitly selected application source files are read from verified layers.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
import tarfile

ROOT = Path(__file__).resolve().parents[1]
DIGEST = 'f7aaa6935794c7a2ce1ef94ed177fa3f987fd31df8df7d6b56f644242922a590'
IMAGE = 'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:' + DIGEST
SCAN = ROOT / '.work/alpha-ms-distribution-v14r13-20261007/scan-v1'
EXISTING = (
    'scripts/generate_month_end_package.py', 'scripts/render_onboarding_historical_reports.py',
    'scripts/run_self_service_onboarding_reports.py', 'scripts/render_monthly_presentation.mjs',
    'scripts/validate_monthly_presentation.py', 'src/ibkr_etl/presentation_design.py',
    'src/ibkr_etl/portfolio_assurance.py',
)


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def verified_layers():
    manifest_path = SCAN / DIGEST
    if sha(manifest_path) != DIGEST:
        raise ValueError('Cached image manifest differs from the installed digest')
    manifest = json.loads(manifest_path.read_bytes())
    config_digest = manifest['config']['digest'].removeprefix('sha256:')
    config_path = SCAN / config_digest
    if sha(config_path) != config_digest:
        raise ValueError('Cached image config digest differs')
    config = json.loads(config_path.read_bytes())
    diff_ids = config['rootfs']['diff_ids']
    if len(diff_ids) != len(manifest['layers']):
        raise ValueError('Layer inventory differs')
    paths = [SCAN / f'layer-{index}.tar' for index in range(len(diff_ids))]
    for path, digest in zip(paths, diff_ids):
        if 'sha256:' + sha(path) != digest:
            raise ValueError('Cached uncompressed layer integrity differs: ' + path.name)
    return paths, config_digest, diff_ids


def read_installed_sources(layers, names):
    wanted = {'app/' + name: name for name in names}
    result = {}
    for layer in reversed(layers):
        if not wanted:
            break
        with tarfile.open(layer, 'r:') as archive:
            members = {m.name.removeprefix('./').lstrip('/'): m for m in archive.getmembers()}
            for archive_path, name in list(wanted.items()):
                # Fail closed rather than guessing through deleted/opaque dirs.
                path = PurePosixPath(archive_path)
                blockers = [str(path.parent / ('.wh.' + path.name))]
                blockers.extend(str(parent / '.wh..wh..opq') for parent in path.parents)
                blockers.extend(str(parent.parent / ('.wh.' + parent.name))
                                for parent in path.parents if parent.name)
                member = members.get(archive_path)
                if member is not None:
                    if not member.isfile() or member.size > 2 * 1024 * 1024:
                        raise ValueError('Selected source must be a bounded regular file')
                    result[name] = archive.extractfile(member).read()
                    del wanted[archive_path]
                elif any(blocker in members for blocker in blockers):
                    raise ValueError('Selected source was removed/hidden by an image layer')
    if wanted:
        raise ValueError('Selected installed sources missing: ' + ', '.join(wanted))
    return result


def once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError('Exact scoped patch anchor differs: ' + old[:100])
    return text.replace(old, new)


def monthly_patch(base: str) -> str:
    """Apply only the shared gate/contract, not the website design-update draft."""
    base = once(base,
        '    PLATFORM_PRESENTATION_CONTENT_CONTRACT_VERSION,\n    PLATFORM_PRESENTATION_CONTENT_REQUIREMENTS,\n    platform_content_expectations,',
        '    presentation_content_contract_version,\n    presentation_content_requirements,')
    base = once(base, 'from validate_monthly_presentation import validate as validate_presentation',
                'from ms_review_upload import upload_ms_review\nfrom validate_subject_presentation import validate_subject_presentation')
    base = once(base, 'PRESENTATION_LAYOUT_VERSION = PLATFORM_PRESENTATION_CONTENT_CONTRACT_VERSION\n', '')
    base = once(base, '        template_versions = {',
                '        presentation_layout = presentation_content_contract_version(account_id)\n        template_versions = {')
    base = base.replace('PRESENTATION_LAYOUT_VERSION', 'presentation_layout')
    base = base.replace('PLATFORM_PRESENTATION_CONTENT_REQUIREMENTS', 'presentation_content_requirements(account_id)')
    start = base.index('            presentation_expectations = platform_content_expectations(payload)')
    end = base.index('\n\n            artifacts = {', start)
    base = base[:start] + '''            presentation_validation = validate_subject_presentation(
                presentation_output, snapshot_file,
                subject_id=account_id, period_end=period_end.isoformat(),
                expected_strategy=strategy_name or None,
            )
            if (presentation_validation["snapshot_sha256"] != snapshot_sha
                    or presentation_validation["content_contract_version"] != presentation_layout):
                raise RuntimeError("Presentation validation differs from the frozen input/content contract")''' + base[end:]
    base = once(base, '                artifact["sha256"] = _sha256(content)',
        '''                artifact["sha256"] = _sha256(content)
                if kind == "presentation" and artifact["sha256"] != presentation_validation["output_sha256"]:
                    raise RuntimeError("Presentation changed after output validation")''')
    # Keep generic storage behaviour intact. New MS review bytes are create-only
    # and hash-addressed, without deploying the unrelated regeneration feature.
    base = once(base, 'f"review/{account_id}/{period_end.isoformat()}/{path.name}"',
        '''f"review/{account_id}/{period_end.isoformat()}/{artifact['sha256']}-{path.name}"
                    if account_id == "alpha-world-ms" else f"review/{account_id}/{period_end.isoformat()}/{path.name}"''')
    start = base.index('                uploaded = upload_bytes(')
    end = base.index('\n                if not uploaded:', start)
    old_upload = base[start:end]
    base = base[:start] + '''                if account_id == "alpha-world-ms":
                    uploaded = upload_ms_review(storage_url, container, artifact["blob_name"],
                        content, content_type=artifact["content_type"])
                else:
''' + '\n'.join('    ' + line for line in old_upload.splitlines()) + base[end:]
    # This guard rejects an accidental broad copy of the dirty working tree.
    if any(term in base for term in ('report_design_updates', 'REPORT_DESIGN_UPDATES_ENABLED',
                                    'qualified_report_designs', '_pending_design_update', 'allow_replacement')):
        raise ValueError('Unrelated report-design functionality leaked into the candidate')
    ast.parse(base)
    return base


def assemble(folder: Path):
    record = json.loads((folder / 'baseline-binding.json').read_bytes())
    if record['base_image'] != IMAGE:
        raise ValueError('Installed baseline image changed')
    for name, digest in record['installed_sources_sha256'].items():
        if sha(folder / 'baseline' / name) != digest:
            raise ValueError('Extracted installed baseline changed')
    context = folder / 'context'
    if context.exists():
        raise ValueError('Preserve the frozen candidate; select a new attempt')
    worker_receipt = ROOT / '.work/alpha-ms-presentation-all-months-20261007-r4/worker-output-gate-v1.json'
    qualification = json.loads(worker_receipt.read_bytes())
    if qualification['status'] != 'local_worker_output_gate_passed' or qualification['months_verified'] != 21:
        raise ValueError('Complete private worker-gate qualification is required')
    for name, digest in qualification['code_binding'].items():
        if sha(ROOT / name) != digest:
            raise ValueError('Previously qualified worker code changed: ' + name)
    files = {}
    for name in EXISTING:
        if name.endswith('/generate_month_end_package.py'):
            content = monthly_patch((folder / 'baseline' / name).read_text(encoding='utf-8')).encode()
        else:
            content = (ROOT / name).read_bytes()
        if name.endswith('/render_monthly_presentation.mjs'):
            source = content.decode()
            source = once(source, ': row.description || row.symbol,', ': row.description,')
            content = source.encode()  # Restore the installed generic-account caption path.
        files[name] = content
    for name in ('src/ibkr_etl/alpha_world_ms_presentation.py', 'scripts/audit_alpha_ms_presentation.py',
                 'scripts/validate_subject_presentation.py', 'scripts/ms_review_upload.py'):
        files[name] = (ROOT / name).read_bytes()
    tests = ('test_subject_presentation_validation.py', 'test_ms_presentation_model.py',
             'test_ms_presentation_audit.py', 'test_presentation_design.py',
             'test_ms_registered_batch.py', 'test_month_end_package_readiness.py',
             'test_onboarding_reports.py', 'test_portfolio_assurance.py',
             'test_ms_review_upload.py', 'test_ms_presentation_worker_runtime.py')
    for name in tests:
        files['tests/' + name] = (ROOT / 'tests' / name).read_bytes()
    docker = f'''FROM {IMAGE}
ENV PYTHONPATH=/app/src:/app:/app/scripts
COPY --chown=10001:10001 src/ibkr_etl/ /app/src/ibkr_etl/
COPY --chown=10001:10001 scripts/ /app/scripts/
COPY --chown=10001:10001 tests/ /app/ms-presentation-worker-tests/
COPY --chown=10001:10001 MS_PRESENTATION_WORKER_RELEASE.json /app/MS_PRESENTATION_WORKER_RELEASE.json
RUN ["python", "scripts/check_report_runtime.py"]
RUN ["python", "-m", "unittest", "discover", "-s", "/app/ms-presentation-worker-tests", "-v"]
RUN ["python", "-c", "import generate_month_end_package as m,render_onboarding_historical_reports as h;from validate_subject_presentation import validate_subject_presentation as v;assert m.validate_subject_presentation is v and h.validate_subject_presentation is v;print('PASS: scoped MS presentation worker contract')"]
CMD ["python", "scripts/run_registry_month_end_packages.py"]
'''
    files['Dockerfile'] = docker.encode()
    release = {'base_image': IMAGE, 'content_contract_version': qualification['content_contract_version'],
               'worker_qualification_sha256': sha(worker_receipt),
               'installed_files_sha256': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()
                                          if name.startswith(('src/', 'scripts/'))},
               'report_design_updates_included': False, 'website_cutover': False,
               'source_data_changed': False, 'report_release_authorized': False,
               'onboarding_activated': False}
    files['MS_PRESENTATION_WORKER_RELEASE.json'] = (json.dumps(release, indent=2) + '\n').encode()
    for name, content in files.items():
        target = context / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(content)
    intent = {'status': 'scoped_local_candidate_frozen', 'base_image': IMAGE,
              'tag': 'ibkr-platform:' + folder.name,
              'files_sha256': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
              'baseline_binding_sha256': sha(folder / 'baseline-binding.json'),
              'preparer_sha256': sha(Path(__file__)), 'release': release,
              'build_dispatched': False, 'azure_changed': False}
    with (folder / 'source-intent.json').open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(intent, indent=2) + '\n')
    print(json.dumps({'status': intent['status'], 'files': len(files), 'base_image': IMAGE,
                      'report_design_updates_included': False, 'azure_changed': False}))


def materialize_local_runtime(folder: Path):
    """Reconstruct only installed app code, then overlay the frozen candidate.

    This prevents local imports from silently using unrelated repository code.
    Application secrets, external dependencies and arbitrary archive paths are
    deliberately excluded. The configured Python environment supplies libraries.
    """
    target = folder / 'local-runtime'
    if target.exists():
        raise ValueError('Preserve the existing local runtime')
    intent = json.loads((folder / 'source-intent.json').read_bytes())
    for name, digest in intent['files_sha256'].items():
        if sha(folder / 'context' / name) != digest:
            raise ValueError('Frozen context changed')
    layers, _, _ = verified_layers()
    names = set()
    for layer in layers:
        with tarfile.open(layer, 'r:') as archive:
            for member in archive.getmembers():
                name = member.name.removeprefix('./').lstrip('/')
                if re.fullmatch(r'app/(?:src/ibkr_etl/\w+\.py|scripts/\w+\.(?:py|mjs))', name):
                    names.add(name.removeprefix('app/'))
    installed = read_installed_sources(layers, sorted(names))
    for name, data in installed.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    for name in intent['files_sha256']:
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((folder / 'context' / name).read_bytes())
    name = 'templates/U16088571/approved-august-2026-20-slide-v24.pptx'
    template = read_installed_sources(layers, [name])[name]
    if hashlib.sha256(template).hexdigest() != '4393eda47945c9df17167e8cbe5278afe90f20b16f52bd473ff3e7549a759191':
        raise ValueError('Installed physical template differs from the approved identity')
    path = target / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(template)
    with (folder / 'local-runtime-binding.json').open('x', encoding='utf-8') as stream:
        json.dump({'base_image': IMAGE, 'installed_app_sources': len(installed),
                   'files_sha256': {p.relative_to(target).as_posix(): sha(p)
                                    for p in target.rglob('*') if p.is_file()},
                   'repository_fallback_allowed': False}, stream, indent=2)
    print(json.dumps({'local_runtime_materialized': True, 'installed_app_sources': len(installed),
                      'repository_fallback_allowed': False, 'azure_changed': False}))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--folder', type=Path, required=True)
    parser.add_argument('--extract-baseline-only', action='store_true')
    parser.add_argument('--local-runtime', action='store_true')
    args = parser.parse_args()
    if args.local_runtime:
        materialize_local_runtime(args.folder)
        return 0
    if not args.extract_baseline_only:
        assemble(args.folder)
        return 0
    if args.folder.exists():
        raise ValueError('Preserve existing attempt; select a new folder')
    layers, config_digest, diff_ids = verified_layers()
    data = read_installed_sources(layers, EXISTING)
    args.folder.mkdir(parents=True)
    hashes = {}
    for name, content in data.items():
        target = args.folder / 'baseline' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(content)
        hashes[name] = hashlib.sha256(content).hexdigest()
    record = {'status': 'installed_source_baseline_verified', 'base_image': IMAGE,
              'config_digest': config_digest, 'layer_diff_ids': diff_ids,
              'installed_sources_sha256': hashes, 'candidate_prepared': False,
              'azure_changed': False, 'source_data_changed': False}
    with (args.folder / 'baseline-binding.json').open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(record, indent=2) + '\n')
    print(json.dumps({key: value for key, value in record.items()
                      if key not in ('installed_sources_sha256', 'layer_diff_ids')}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
