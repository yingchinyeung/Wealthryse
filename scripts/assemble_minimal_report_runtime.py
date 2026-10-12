"""Export a reporting candidate's exact runtime, retaining native provenance.

This is not the risk runtime. The default retains LZMA, ZIP, SQLite and fonts.
An explicit PDF/PPTX-only build excludes the unused native LZMA archive decoder;
it must not be used for a general archive-processing or website workload.
No production resource, received IBKR observation or report is modified.
The final image has no OS administration utilities. Every retained library and
font keeps its original package owner/version, including unresolved findings.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from ibkr_etl.minimal_runtime import (
    ldd_dependencies, library_package_names, omitted_extension, selected_package_status,
)


def report_extension_omitted(path, *, pdf_pptx_only=False):
    if type(pdf_pptx_only) is not bool:
        raise ValueError('Explicit Boolean reporting build mode required')
    name = Path(path).name
    return omitted_extension(name) or (pdf_pptx_only and name.startswith('_lzma.') and name.endswith('.so'))


def fingerprint_tree(directory):
    """Fingerprint content and links without following directory aliases."""
    result = {}
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            result[path.relative_to(directory).as_posix()] = {'link': os.readlink(path)}
        elif path.is_file():
            result[path.relative_to(directory).as_posix()] = {
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'bytes': path.stat().st_size,
            }
    return result


def main():
    parser = argparse.ArgumentParser(description='Export exact reporting dependencies and provenance')
    parser.add_argument('--pdf-pptx-only', action='store_true',
        help='PDF/PPTX worker only: preserve standard ZIP, omit native LZMA archives')
    args = parser.parse_args()
    def omit(name):
        return report_extension_omitted(name, pdf_pptx_only=args.pdf_pptx_only)
    root = Path('/runtime')
    app = Path('/app')
    if os.getuid() != 0 or os.name != 'posix' or root.exists() or not app.is_dir():
        raise ValueError('Linux builder, application and new exact /runtime required')
    before = fingerprint_tree(app)
    root.mkdir()
    for name in ('lib', 'lib64'):
        source = Path('/') / name
        if source.is_symlink():
            if source.resolve(strict=True) != Path('/usr') / name:
                raise ValueError('Unexpected system library alias')
            (root / 'usr' / name).mkdir(parents=True, exist_ok=True)
            (root / name).symlink_to('usr/' + name)
    shutil.copytree('/usr/local', root / 'usr/local', symlinks=True,
        ignore=lambda directory, names: [n for n in names if omit(n)])
    shutil.copytree(app, root / 'app', symlinks=True)
    # Inspect Python, Node and the renderer's actual native canvas modules.
    elfs = []
    for directory in (Path('/usr/local'), app):
        for path in directory.rglob('*'):
            if path.is_file() and not path.is_symlink() and not omit(path.name):
                with path.open('rb') as handle:
                    if handle.read(4) == b'\x7fELF':
                        elfs.append(path)
    inspection_paths = sorted({str(p.parent) for p in elfs})
    inspection_env = dict(os.environ, LD_LIBRARY_PATH=':'.join(inspection_paths))
    dependencies = set()
    for path in sorted(elfs):
        result = subprocess.run(['ldd', str(path)], env=inspection_env,
            capture_output=True, text=True, timeout=30)
        if result.returncode and 'not a dynamic executable' not in result.stdout + result.stderr:
            raise ValueError('Reporting ELF dependency inspection failed: ' + str(path))
        dependencies.update(ldd_dependencies(result.stdout))
    owners = {}
    for listing in Path('/var/lib/dpkg/info').glob('*.list'):
        package = listing.name[:-5].split(':')[0]
        for name in listing.read_text().splitlines():
            path = Path(name)
            if path.is_file():
                owners.setdefault(str(path.resolve(strict=True)), set()).add(package)
    copied, packages = {}, {'ca-certificates', 'tzdata', 'base-files'}
    for name in sorted(dependencies):
        source = Path(name)
        real = source.resolve(strict=True)
        if real.is_relative_to('/usr/local'):
            if not (root / real.relative_to('/')).is_file():
                raise ValueError('Missing reporting internal dependency')
            continue
        package_names = library_package_names(owners.get(str(real), set()))
        packages.update(package_names)
        target = root / real.relative_to('/')
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(real, target)
        alias = root / source.relative_to('/')
        if alias != target and not alias.exists():
            alias.parent.mkdir(parents=True, exist_ok=True)
            alias.symlink_to('/' + real.relative_to('/').as_posix())
        copied[name] = {'sha256': hashlib.sha256(real.read_bytes()).hexdigest(),
                        'source_path': str(real), 'packages': package_names}
    for name in ('/etc/os-release', '/etc/debian_version', '/etc/nsswitch.conf',
                 '/etc/ssl/certs/ca-certificates.crt'):
        target = root / Path(name).relative_to('/')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(name).resolve(strict=True), target)
    data_trees = {}
    for name in ('/usr/share/zoneinfo', '/usr/share/fonts', '/etc/fonts'):
        source = Path(name)
        if not source.exists():
            if name == '/usr/share/zoneinfo':
                raise ValueError('Timezone database missing')
            data_trees[name] = {'present': False}
            continue
        shutil.copytree(source, root / source.relative_to('/'), symlinks=True)
        data_trees[name] = {'present': True, 'files': fingerprint_tree(source)}
        if name != '/usr/share/zoneinfo':
            for path in source.rglob('*'):
                if path.is_file():
                    packages.update(library_package_names(owners.get(str(path.resolve(strict=True)), set())))
    for package in sorted(packages):
        source = Path('/usr/share/doc') / package / 'copyright'
        if not source.is_file():
            raise ValueError('Original package copyright metadata missing')
        target = root / 'usr/share/doc' / package / 'copyright'
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source.resolve(strict=True), target)
    (root / 'var/lib/dpkg').mkdir(parents=True)
    (root / 'var/lib/dpkg/status').write_text(
        selected_package_status(Path('/var/lib/dpkg/status').read_text(), packages))
    (root / 'etc/passwd').write_text('ibkr:x:10001:10001:IBKR report runtime:/home/ibkr:/usr/sbin/nologin\n')
    (root / 'etc/group').write_text('ibkr:x:10001:\n')
    (root / 'home/ibkr').mkdir(parents=True)
    os.chown(root / 'home/ibkr', 10001, 10001)
    (root / 'tmp').mkdir(mode=0o1777)
    os.chmod(root / 'tmp', 0o1777)
    if before != fingerprint_tree(app) or before != fingerprint_tree(root / 'app'):
        raise ValueError('Application or template bytes changed during export')
    record = {'schema': 'wealthryse-minimal-report-runtime-v1',
        'os_packages': sorted(packages), 'copied_os_libraries': copied,
        'application_tree_sha256': hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(),
        'application_files': len(before), 'runtime_data_trees': data_trees,
        'lzma_included': not args.pdf_pptx_only, 'pdf_pptx_only': args.pdf_pptx_only,
        'generic_lzma_archive_inputs_supported': not args.pdf_pptx_only,
        'standard_zip_included': True, 'sqlite_included': True, 'node_included': True,
        'package_status_is_original': True, 'copied_packages_are_runtime_subsets': True,
        'application_and_templates_unchanged': True,
        'security_scan_passed': False, 'financial_or_visual_acceptance': False,
        'production_authorized': False}
    (root / 'REPORT_RUNTIME_PROVENANCE.json').write_text(json.dumps(record, sort_keys=True, indent=2))
    print(json.dumps({'exported': True, 'os_packages': sorted(packages),
        'application_files': len(before), 'application_tree_sha256': record['application_tree_sha256'],
        'templates_unchanged': True, 'lzma_included': not args.pdf_pptx_only,
        'pdf_pptx_only': args.pdf_pptx_only, 'production_authorized': False}), flush=True)


if __name__ == '__main__':
    main()
