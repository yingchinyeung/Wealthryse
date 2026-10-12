"""Export the reviewed Linux runtime into empty /runtime during Docker build.

Copies actual ELF dependencies and their unmodified Debian package provenance.
Every copied OS library retains all declared owners; unresolved links fail.
No production resources, credentials or received IBKR data are involved.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from ibkr_etl.minimal_runtime import omitted_extension, ldd_dependencies, selected_package_status, library_package_names


def main():
    parser=argparse.ArgumentParser(description='Export reviewed ELF dependencies and original package provenance')
    parser.add_argument('--database-risk-only',action='store_true',
        help='Omit the LZMA archive decoder only for the database-observation risk worker')
    args=parser.parse_args()
    def omit(name):
        return omitted_extension(name,database_risk_only=args.database_risk_only)
    root = Path('/runtime')
    if os.getuid() != 0 or os.name != 'posix' or root.exists():
        raise ValueError('Linux build root and a new exact /runtime destination required')
    root.mkdir()
    # Preserve the builder's usr-merge aliases, not links to the build host.
    for name in ('lib', 'lib64'):
        source = Path('/') / name
        if source.is_symlink():
            if source.resolve(strict=True) != Path('/usr') / name:
                raise ValueError('Unexpected system library directory alias')
            (root / 'usr' / name).mkdir(parents=True, exist_ok=True)
            (root / name).symlink_to('usr/' + name)
    destination = root / 'usr/local'
    shutil.copytree('/usr/local', destination, symlinks=True,
        ignore=lambda directory, names: [n for n in names if omit(n) or n == '__pycache__'])
    elfs = []
    for source in Path('/usr/local').rglob('*'):
        if source.is_file() and not source.is_symlink() and not omit(source.name):
            with source.open('rb') as handle:
                if handle.read(4) == b'\x7fELF':
                    elfs.append(source)
    dependencies = set()
    # Some wheel libraries are normally loaded by a parent extension's RPATH.
    # Inspecting those shared objects individually needs the wheel directories;
    # this environment is not added to the final runtime's loader configuration.
    inspection_paths = sorted({str(p.parent) for p in elfs})
    inspection_env = dict(os.environ, LD_LIBRARY_PATH=':'.join(inspection_paths))
    for source in sorted(elfs):
        result = subprocess.run(['ldd', str(source)], env=inspection_env,
            capture_output=True, text=True, timeout=30)
        if result.returncode and 'not a dynamic executable' not in result.stdout + result.stderr:
            raise ValueError('ELF dependency inspection failed')
        try:
            dependencies.update(ldd_dependencies(result.stdout))
        except ValueError as error:
            raise ValueError('ELF dependency check failed for '+str(source)+': '+result.stdout) from error
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
        if not any(real.is_relative_to(p) for p in ('/usr/local', '/usr/lib', '/lib', '/lib64')):
            raise ValueError('Resolved ELF library outside reviewed runtime roots')
        if real.is_relative_to('/usr/local'):
            if not (root / real.relative_to('/')).is_file():
                raise ValueError('Runtime-internal dependency was not copied')
            continue
        package_names = library_package_names(owners.get(str(real), set()))
        packages.update(package_names)
        target = root / real.relative_to('/')
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(real, target)
        alias = root / source.relative_to('/')
        if str(alias) != str(target) and not alias.exists():
            alias.parent.mkdir(parents=True, exist_ok=True)
            alias.symlink_to('/' + real.relative_to('/').as_posix())
        copied[name] = {'sha256': hashlib.sha256(real.read_bytes()).hexdigest(),
                        'source_path': str(real), 'packages': package_names}
    # Trivy's Debian analyzer requires debian_version, not just os-release.
    # Copy original base-files bytes; never synthesize a scanner identity.
    for name in ('/etc/os-release', '/etc/debian_version', '/etc/nsswitch.conf', '/etc/ssl/certs/ca-certificates.crt'):
        target = root / Path(name).relative_to('/')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(name).resolve(strict=True), target)
    shutil.copytree('/usr/share/zoneinfo', root / 'usr/share/zoneinfo', symlinks=True)
    status = selected_package_status(Path('/var/lib/dpkg/status').read_text(), packages)
    for package in sorted(packages):
        copyright_file = Path('/usr/share/doc') / package / 'copyright'
        if not copyright_file.is_file():
            raise ValueError('Original package copyright metadata missing')
        target = root / 'usr/share/doc' / package / 'copyright'
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(copyright_file.resolve(strict=True), target)
    (root / 'var/lib/dpkg').mkdir(parents=True)
    (root / 'var/lib/dpkg/status').write_text(status)
    (root / 'etc/passwd').write_text('ibkr:x:10001:10001:IBKR runtime:/home/ibkr:/usr/sbin/nologin\n')
    (root / 'etc/group').write_text('ibkr:x:10001:\n')
    (root / 'home/ibkr').mkdir(parents=True)
    os.chown(root / 'home/ibkr', 10001, 10001)
    (root / 'tmp').mkdir(mode=0o1777)
    os.chmod(root / 'tmp', 0o1777)
    record = {'schema':'wealthryse-minimal-risk-runtime-v1',
        'os_packages': sorted(packages), 'copied_os_libraries': copied,
        'inspection_only_wheel_library_directories': inspection_paths,
        'terminal_curses_and_native_uuid_extensions_included': False,
        'terminal_readline_and_gui_tk_extensions_included': False,
        'uuid_python_fallback_included': True, 'lzma_included': not args.database_risk_only,
        'database_risk_only':args.database_risk_only,
        'archive_file_input_supported':not args.database_risk_only,
        'sqlite_included':True,
        'package_status_is_original': True, 'copied_packages_are_runtime_subsets': True,
        'security_scan_passed': False, 'production_authorized': False}
    (root / 'RUNTIME_PROVENANCE.json').write_text(json.dumps(record, sort_keys=True, indent=2))
    print(json.dumps(record), flush=True)


if __name__ == '__main__':
    main()
