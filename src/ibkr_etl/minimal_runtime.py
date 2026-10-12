"""Strict helpers for an explicit, provenance-preserving risk runtime export.

This is a build helper, not production authorization or a vulnerability filter.
The exported risk worker has no terminal/GUI or native libuuid extension.
Python's UUID fallback is retained. LZMA remains in the default export; the
explicit database-only risk build omits its archive decoder. SQLite and all
numerical/TLS libraries remain in both modes.
"""
import re
import posixpath
from pathlib import PurePosixPath


OMITTED_EXTENSIONS = ('_curses', '_curses_panel', '_uuid', '_tkinter', 'readline')


def omitted_extension(path, *, database_risk_only=False):
    if type(database_risk_only) is not bool:
        raise ValueError('Explicit Boolean database-only build mode required')
    name = PurePosixPath(path).name
    omitted=OMITTED_EXTENSIONS + (('_lzma',) if database_risk_only else ())
    return any(name.startswith(stem + '.') and name.endswith('.so') for stem in omitted)


def ldd_dependencies(output):
    if 'not found' in output:
        raise ValueError('Unresolved dynamic dependency')
    result = set()
    for line in output.splitlines():
        text = line.strip()
        if not text or text in ('statically linked', 'not a dynamic executable'):
            continue
        if text.startswith('linux-vdso.'):
            continue
        match = re.fullmatch(r'(?:[^\s]+\s+=>\s+)?(/[^\s]+)\s+\(0x[0-9a-fA-F]+\)', text)
        if not match:
            raise ValueError('Unrecognized dependency output')
        path = PurePosixPath(posixpath.normpath(match[1]))
        if not any(
                path.is_relative_to(root) for root in ('/usr/local', '/usr/lib', '/lib', '/lib64')):
            raise ValueError('Dynamic dependency outside reviewed runtime paths')
        result.add(str(path))
    return result


def selected_package_status(status, packages):
    """Retain original version/source records, never synthesize a fixed version."""
    result, seen = [], set()
    for paragraph in status.strip().split('\n\n'):
        fields = dict(line.split(': ', 1) for line in paragraph.splitlines()
                      if ': ' in line and not line.startswith((' ', '\t')))
        name = fields.get('Package')
        if name in packages:
            if name in seen or fields.get('Status') != 'install ok installed' or not fields.get('Version'):
                raise ValueError('Incomplete or duplicate source package record')
            seen.add(name)
            result.append(paragraph)
    if seen != packages:
        raise ValueError('Missing source package provenance')
    return '\n\n'.join(result) + '\n\n'


def library_package_names(owners):
    """Aliases may have several declared owners; retain all, never choose one."""
    if not isinstance(owners, set) or not owners or any(not isinstance(name, str)
            or not re.fullmatch(r'[a-z0-9][a-z0-9+.-]*', name) for name in owners):
        raise ValueError('Every OS library requires original package ownership')
    return sorted(owners)
