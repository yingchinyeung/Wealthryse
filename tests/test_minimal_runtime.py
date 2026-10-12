import unittest
from pathlib import Path
from ibkr_etl.minimal_runtime import omitted_extension, ldd_dependencies, selected_package_status, library_package_names
from ibkr_etl.source_archive import allowed_path


class MinimalRuntimeTests(unittest.TestCase):
    def test_only_explicit_optional_extensions_omitted(self):
        for name in ('_curses.cpython-312-x86_64-linux-gnu.so', '_curses_panel.abi.so', '_uuid.abi.so',
                     '_tkinter.abi.so', 'readline.abi.so'):
            self.assertTrue(omitted_extension(name))
        for name in ('_lzma.abi.so', '_ssl.abi.so', '_uuid.py', 'not_curses.abi.so'):
            self.assertFalse(omitted_extension(name))

    def test_dependency_parser_is_strict(self):
        data = 'linux-vdso.so.1 (0xabc)\nlibc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x12)\n/lib64/ld-linux-x86-64.so.2 (0x45)\n'
        self.assertEqual(ldd_dependencies(data), {'/lib/x86_64-linux-gnu/libc.so.6','/lib64/ld-linux-x86-64.so.2'})
        self.assertEqual(ldd_dependencies('/usr/local/lib/package/../libs/library.so (0x12)'),
                         {'/usr/local/lib/libs/library.so'})
        for bad in ('libc => not found', '/tmp/lib.so (0x12)', '/usr/lib/../secret (0x12)', 'unrecognized'):
            with self.assertRaises(ValueError):
                ldd_dependencies(bad)

    def test_database_only_mode_removes_only_optional_archive_decoder(self):
        self.assertTrue(omitted_extension('_lzma.cpython-312-x86_64-linux-gnu.so',database_risk_only=True))
        self.assertFalse(omitted_extension('_lzma.cpython-312-x86_64-linux-gnu.so'))
        for name in ('_sqlite3.abi.so','_ssl.abi.so','lzma.py','not_lzma.abi.so','_lzma.py'):
            self.assertFalse(omitted_extension(name,database_risk_only=True))
        for bad in (1,0,'true',None,[],{}):
            with self.assertRaises(ValueError):omitted_extension('_lzma.abi.so',database_risk_only=bad)

    def test_database_risk_entrypoint_does_not_accept_archive_files(self):
        import ast
        entry=ast.parse(Path('scripts/build_risk_snapshots.py').read_text())
        imports={alias.name for node in ast.walk(entry) if isinstance(node,ast.Import) for alias in node.names}
        self.assertNotIn('lzma',imports)
        flags={node.args[0].value for node in ast.walk(entry)
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)
            and node.func.attr=='add_argument' and node.args and isinstance(node.args[0],ast.Constant)}
        self.assertEqual(flags,{'--subject','--history-audit-limit','--history-rebuild-limit','--no-history-audit','--all-dates'})
        self.assertIn('Risk snapshot generation requires DATABASE_URL',Path('scripts/build_risk_snapshots.py').read_text())

    def test_original_package_versions_and_source_retained(self):
        status = 'Package: libc6\nStatus: install ok installed\nVersion: 2.36-9+deb12u14\nSource: glibc\n\nPackage: unused\nStatus: install ok installed\nVersion: 1\n\n'
        result = selected_package_status(status, {'libc6'})
        self.assertIn('Version: 2.36-9+deb12u14', result)
        self.assertIn('Source: glibc', result)
        self.assertNotIn('Package: unused', result)
        for bad, packages in ((status, {'missing'}), (status+status, {'libc6'}),
                (status.replace('install ok installed','deinstall ok config-files'), {'libc6'})):
            with self.assertRaises(ValueError):
                selected_package_status(bad, packages)

    def test_shared_loader_retains_every_owner_and_unowned_fails(self):
        self.assertEqual(library_package_names({'libc6','libc-bin'}),['libc-bin','libc6'])
        self.assertEqual(library_package_names({'libssl3'}),['libssl3'])
        for bad in (set(), {'not/a-package'}, {'LIBC'}, ['libc6'], {None}):
            with self.assertRaises(ValueError):
                library_package_names(bad)

    def test_recipe_has_no_mutable_final_base_or_shell(self):
        recipe = Path('Dockerfile.risk.minimal').read_text()
        self.assertIn('bookworm@sha256:', recipe)
        self.assertIn('FROM scratch', recipe)
        self.assertIn('USER 10001:10001', recipe)
        self.assertIn('RUN ["python", "scripts/check_risk_runtime.py"]', recipe)
        self.assertIn('--require-hashes', recipe)
        self.assertNotIn('COPY . ', recipe)
        self.assertTrue(allowed_path('Dockerfile.risk.minimal'))
        self.assertFalse(allowed_path('Dockerfile.unreviewed'))

    def test_scanner_os_identity_copied_from_original_base(self):
        assembly = Path('scripts/assemble_minimal_risk_runtime.py').read_text()
        self.assertIn("'/etc/debian_version'", assembly)
        self.assertIn('shutil.copy2(Path(name).resolve(strict=True), target)', assembly)
        self.assertNotIn("(root / 'etc/debian_version').write_text", assembly)


if __name__ == '__main__':
    unittest.main()
