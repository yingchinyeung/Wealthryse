"""Write and check requirements-research.lock, the research image's pinned packages.

The lock holds what the research job needs from requirements.lock, copied entry for entry
(same versions, markers and hashes), plus scipy and pyarrow, which only the research image
has. numpy is already in requirements.lock.

  --check   offline: the copied entries still match requirements.lock, and every entry has
            hashes. Exit 0 when the lock is ready to build, 2 when an added package has
            no hashes yet, 1 when the lock is out of step with requirements.lock.
  --write   offline: rewrite the lock from requirements.lock, keeping the hashes the added
            packages already have.
  --fill    reads the hashes of the added packages' files from PyPI (https://pypi.org/pypi/
            <name>/<version>/json) and writes them in. Needs network; run it on the admin
            VM or a workstation, never in CI, and review the diff before committing.

The image installs the lock with ``pip install --require-hashes --only-binary=:all:``, so a
missing or wrong hash stops the build.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLATFORM_LOCK = ROOT / "requirements.lock"
RESEARCH_LOCK = ROOT / "requirements-research.lock"
# From requirements.lock: Postgres, Azure sign-in and blob storage, and their dependencies.
FROM_PLATFORM = (
    "azure-core", "azure-identity", "azure-storage-blob", "certifi", "cffi", "charset-normalizer",
    "cryptography", "idna", "isodate", "msal", "msal-extensions", "numpy", "psycopg", "psycopg-binary",
    "pycparser", "pyjwt", "requests", "typing-extensions", "tzdata", "urllib3",
)
# Only in the research image. Versions chosen to work with numpy 2.5 on Python 3.12
# (scipy 1.16 needs numpy >= 1.25.2 and < 2.6; pyarrow has no required dependencies).
ADDED = {"scipy": "1.16.1", "pyarrow": "21.0.0"}
HEADER = """\
# The research image's packages (Dockerfile.research). Not used by any other image.
# Entries from requirements.lock are copied unchanged by scripts/research/lock_research_requirements.py
# --write; scipy and pyarrow are added there and their hashes filled with --fill.
"""
REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)")
HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})")


def _normal(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def entries(text: str) -> list[dict]:
    """Each requirement of a lock: its name, version, first line and hashes, in order."""
    found: list[dict] = []
    for line in text.splitlines():
        match = REQUIREMENT.match(line)
        if match:
            found.append({"name": _normal(match.group(1)), "version": match.group(2), "line": line.rstrip(" \\"),
                          "hashes": []})
        elif found and line.startswith("    --hash="):
            found[-1]["hashes"].extend(HASH.findall(line))
    return found


def render(platform_text: str, added_hashes: dict[str, list[str]]) -> str:
    platform = entries(platform_text)
    lines = [HEADER.rstrip("\n")]
    missing = [name for name in FROM_PLATFORM if not any(item["name"] == name for item in platform)]
    if missing:
        raise SystemExit(f"requirements.lock no longer has {', '.join(missing)}")
    blocks = [item for item in platform if item["name"] in FROM_PLATFORM]
    blocks += [{"name": name, "version": version, "line": f"{name}=={version}", "hashes": added_hashes.get(name, [])}
               for name, version in ADDED.items()]
    for item in sorted(blocks, key=lambda item: (item["name"], item["line"])):
        if not item["hashes"]:
            lines.append(item["line"])
            lines.append("    # hashes missing: run scripts/research/lock_research_requirements.py --fill")
            continue
        lines.append(item["line"] + " \\")
        hashes = sorted(set(item["hashes"]))
        lines.extend(f"    --hash=sha256:{digest}" + (" \\" if index < len(hashes) - 1 else "")
                     for index, digest in enumerate(hashes))
    return "\n".join(lines) + "\n"


def added_hashes(lock_text: str) -> dict[str, list[str]]:
    return {item["name"]: item["hashes"] for item in entries(lock_text)
            if item["name"] in ADDED and item["version"] == ADDED[item["name"]]}


def check(platform_text: str, lock_text: str) -> tuple[int, dict]:
    expected = render(platform_text, added_hashes(lock_text))
    unhashed = sorted(item["name"] for item in entries(lock_text) if not item["hashes"])
    names = sorted({item["name"] for item in entries(lock_text)})
    result = {"in_step_with_requirements_lock": expected == lock_text, "packages": names, "without_hashes": unhashed}
    if expected != lock_text:
        return 1, result
    return (2 if unhashed else 0), result


def pypi_hashes(name: str, version: str, *, opener=urllib.request.urlopen) -> list[str]:
    """The sha256 of every file PyPI publishes for that release (wheels and source)."""
    with opener(f"https://pypi.org/pypi/{name}/{version}/json", timeout=60) as response:
        release = json.loads(response.read().decode("utf-8"))
    hashes = sorted({item["digests"]["sha256"] for item in release.get("urls", []) if not item.get("yanked")})
    if not hashes:
        raise SystemExit(f"PyPI lists no files for {name} {version}")
    return hashes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--fill", action="store_true")
    args = parser.parse_args(argv)
    platform_text = PLATFORM_LOCK.read_text(encoding="utf-8")
    lock_text = RESEARCH_LOCK.read_text(encoding="utf-8") if RESEARCH_LOCK.is_file() else ""
    if args.check:
        code, result = check(platform_text, lock_text)
        print("RESEARCH_LOCK " + json.dumps({"ready_to_build": code == 0, **result}, sort_keys=True))
        return code
    hashes = added_hashes(lock_text)
    if args.fill:
        hashes = {name: pypi_hashes(name, version) for name, version in ADDED.items()}
    RESEARCH_LOCK.write_text(render(platform_text, hashes), encoding="utf-8")
    code, result = check(platform_text, RESEARCH_LOCK.read_text(encoding="utf-8"))
    print("RESEARCH_LOCK " + json.dumps({"written": str(RESEARCH_LOCK.relative_to(ROOT)), "ready_to_build": code == 0,
                                         **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
