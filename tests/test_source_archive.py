import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from ibkr_etl.source_archive import allowed_path, build_archive, check_source, sha256, verify_archive


class SourceArchiveTests(unittest.TestCase):
    def fixture(self, root):
        (root / "src").mkdir()
        (root / "src/app.py").write_text("value = 1\n")
        return build_archive(root, ["src/app.py"])

    def test_reproducible_and_never_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data, manifest = self.fixture(root)
            self.assertEqual(data, build_archive(root, ["src/app.py"])[0])
            self.assertEqual(manifest, verify_archive(data, sha256(data)))
            self.assertIs(manifest["deployment_authorized"], False)

    def test_private_and_traversal_paths_rejected(self):
        for path in [".env", "src/../.env", "src//app.py", "/src/app.py", "C:/src/app.py",
                     "src\\app.py", "scripts/node_modules/a.py", "output/report.pdf",
                     "private-inputs/data.py", "tests/data.xml", "docs/secret.md", "src/.git/a.py"]:
            with self.subTest(path=path):
                self.assertFalse(allowed_path(path))

    def test_content_change_changes_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original, _ = self.fixture(root)
            (root / "src/app.py").write_text("value = 2\n")
            changed, _ = build_archive(root, ["src/app.py"])
            self.assertNotEqual(sha256(original), sha256(changed))
            with self.assertRaises(ValueError):
                verify_archive(changed, sha256(original))

    def test_secret_and_binary_fail_without_echoing_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            for content in [b"\x00binary", b"\xff", b"url='postgresql://" + b"realuser:longsecret@host/db'",
                            b"FLEX_TOKEN = '" + b"X" * 30 + b"'"]:
                (root / "src/app.py").write_bytes(content)
                with self.assertRaises(ValueError) as error:
                    build_archive(root, ["src/app.py"])
                self.assertNotIn("longsecret", str(error.exception))

    def test_tampered_manifest_rejected_even_with_updated_zip_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            data, _ = self.fixture(Path(directory))
            stream = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(data)) as original, zipfile.ZipFile(stream, "w") as out:
                for name in original.namelist():
                    content = original.read(name)
                    if name == "SOURCE_MANIFEST.json":
                        manifest = json.loads(content)
                        manifest["deployment_authorized"] = True
                        content = json.dumps(manifest).encode()
                    out.writestr(name, content)
            tampered = stream.getvalue()
            with self.assertRaises(ValueError):
                verify_archive(tampered, sha256(tampered))

    def test_extra_file_not_in_manifest_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            data, _ = self.fixture(Path(directory))
            stream = io.BytesIO(data)
            with zipfile.ZipFile(stream, "a") as archive:
                archive.writestr("src/extra.py", "bad = 1")
            with self.assertRaises(ValueError):
                verify_archive(stream.getvalue(), sha256(stream.getvalue()))

    def test_only_exact_reviewed_parameter_and_fixture_exceptions(self):
        check_source("infra/test.bicep", b"url='postgresql:" + b"//${user}:${uriComponent(password)}@${server}'")
        check_source("tests/test_example.py", b'flex_token="not-a-real-token"')
        with self.assertRaises(ValueError):
            check_source("src/app.py", b'flex_token="not-a-real-token"')
        with self.assertRaises(ValueError):
            check_source("infra/test.bicep", b"url='postgresql:" + b"//${user}:embedded-password@${server}'")


if __name__ == "__main__":
    unittest.main()
