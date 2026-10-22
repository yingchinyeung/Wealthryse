import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


remote = load("website_overlay_build_remote", ROOT / "deploy" / "website_overlay_build_remote.py")
release = load("website_release", ROOT / "deploy" / "website_release.py")


class ApplyReplacementsTest(unittest.TestCase):
    def package(self, files):
        folder = Path(tempfile.mkdtemp())
        for name, text in files.items():
            (folder / name).write_text(text)
        return folder

    def test_replaces_each_old_string_once(self):
        package = self.package({
            "monthly_artifacts.py": 'A = "old-a"\nB = "old-b"\n',
            "presentation_design.py": 'P = "old-p"\n',
            "web.py": "# mentions old-a in a comment\n",
        })
        touched, elsewhere = remote.apply_replacements(package, [
            {"file": "monthly_artifacts.py", "old": "old-a", "new": "new-a"},
            {"file": "monthly_artifacts.py", "old": "old-b", "new": "new-b"},
            {"file": None, "old": "old-p", "new": "new-p"},
        ])
        self.assertEqual(touched, {"monthly_artifacts.py": 2, "presentation_design.py": 1})
        self.assertEqual(elsewhere, {"old-a": ["web.py"]})
        self.assertEqual((package / "monthly_artifacts.py").read_text(), 'A = "new-a"\nB = "new-b"\n')
        self.assertEqual((package / "presentation_design.py").read_text(), 'P = "new-p"\n')
        self.assertEqual((package / "web.py").read_text(), "# mentions old-a in a comment\n")

    def test_refuses_missing_or_repeated_strings_without_writing(self):
        for files, item in (
            ({"monthly_artifacts.py": 'A = "x"\n'}, {"file": "monthly_artifacts.py", "old": "old-a", "new": "n"}),
            ({"monthly_artifacts.py": '"old-a" "old-a"\n'}, {"file": "monthly_artifacts.py", "old": "old-a", "new": "n"}),
            ({"a.py": '"old-p"\n', "b.py": '"old-p"\n'}, {"file": None, "old": "old-p", "new": "n"}),
        ):
            package = self.package(files)
            with self.assertRaises(ValueError):
                remote.apply_replacements(package, [item])
            for name, text in files.items():
                self.assertEqual((package / name).read_text(), text)


class AssuranceConfigTest(unittest.TestCase):
    def test_config_targets_assurance_tag_from_pinned_image(self):
        config = release.assurance_config("0123456789abcdef")
        self.assertEqual(config["base_image"], release.ASSURANCE_BASE)
        self.assertIn("@sha256:0a5788c04f0202cb", config["base_image"])
        self.assertTrue(config["target_image"].endswith("/ibkr-platform:assurance-layout-0123456789ab"))
        news = {item["new"] for item in config["replacements"]}
        self.assertEqual(news, {
            "approved-august-2026-v9-full-portfolio-history",
            "subject-completed-factsheet-model-v8-full-portfolio-history",
            "platform-standard-v24-reporting-currency-full-nav-v9",
        })

    def test_new_names_match_the_repository_layouts(self):
        import sys
        sys.path.insert(0, str(ROOT / "src"))
        from ibkr_etl.monthly_artifacts import factsheet_layout_version
        from ibkr_etl.presentation_design import presentation_content_contract_version
        for subject in ("U16088571", "U16088606", "hong-kong-china"):
            self.assertIn(f"'{subject}': '{factsheet_layout_version(subject)}'", release.ASSURANCE_CHECK)
            self.assertIn(presentation_content_contract_version(subject), release.ASSURANCE_CHECK)
        compile(release.ASSURANCE_CHECK, "check", "exec")


class RunFailureTest(unittest.TestCase):
    def test_failure_keeps_the_failing_step_output_after_warnings(self):
        import sys
        script = "import sys; sys.stderr.write('DEPRECATED: legacy builder\\n'); print('ModuleNotFoundError: No module named x'); sys.exit(1)"
        with self.assertRaises(RuntimeError) as caught:
            remote.run([sys.executable, "-c", script], "website image build")
        self.assertIn("DEPRECATED", str(caught.exception))
        self.assertIn("No module named x", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
