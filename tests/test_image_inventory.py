import hashlib
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("website_release_inventory", ROOT / "deploy" / "website_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

DIGEST = "a" * 64
IMAGE = release.IMAGE_PREFIX + DIGEST


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


class ImageArgumentTest(unittest.TestCase):
    def test_default_is_the_website(self):
        self.assertEqual(release.image_argument(["web.py"]), (None, ["web.py"]))

    def test_image_by_digest_only(self):
        self.assertEqual(release.image_argument(["--image", IMAGE, "scripts/x.py"]), (IMAGE, ["scripts/x.py"]))
        for bad in (["--image"], ["--image", release.REG + "/ibkr-platform:web-abc"], ["--image", IMAGE[:-1]]):
            with self.assertRaises(SystemExit):
                release.image_argument(bad)

    def test_capture_refuses_paths_outside_the_package_and_scripts(self):
        for names in (["web.py"], ["src/ibkr_etl/../x.py"], ["migrations/a.py"], ["scripts/a.txt"]):
            with mock.patch.object(sys, "argv", ["website_release.py", "capture", "--image", IMAGE, *names]):
                with self.assertRaises(SystemExit):
                    release.capture()


class CompareWithRepoTest(unittest.TestCase):
    def compare(self, hashes):
        files = {"src/ibkr_etl/web.py": "web", "src/ibkr_etl/agent_tools.py": "repo tools",
                 "src/ibkr_etl/only_repo.py": "x", "scripts/run.py": "run"}

        def git(*args, binary=False):
            if args[0] == "ls-tree":
                return "\n".join(p for p in files if p.startswith(args[-1]))
            body = files[args[1].split(":", 1)[1]]
            return body.encode() if binary else body

        with mock.patch.object(release, "git", git):
            return release.compare_with_repo(hashes, "head")

    def test_sorts_files_into_same_different_and_missing(self):
        result = self.compare({"src/ibkr_etl/web.py": sha("web"), "src/ibkr_etl/agent_tools.py": sha("live tools"),
                               "src/ibkr_etl/qualified_agent_risk.py": sha("q"), "scripts/run.py": sha("run")})
        self.assertEqual(result, {"same": ["scripts/run.py", "src/ibkr_etl/web.py"],
                                  "different": ["src/ibkr_etl/agent_tools.py"],
                                  "only_image": ["src/ibkr_etl/qualified_agent_risk.py"],
                                  "only_repo": ["src/ibkr_etl/only_repo.py"]})

    def test_scripts_are_not_reported_missing_when_the_image_has_none(self):
        result = self.compare({"src/ibkr_etl/web.py": sha("web")})
        self.assertNotIn("scripts/run.py", result["only_repo"])


class InventoryArgumentsTest(unittest.TestCase):
    def test_requires_images_by_digest(self):
        for args in ([], [release.REG + "/ibkr-platform:web-x"]):
            with mock.patch.object(sys, "argv", ["website_release.py", "inventory", *args]):
                with self.assertRaises(SystemExit):
                    release.inventory()


if __name__ == "__main__":
    unittest.main()
