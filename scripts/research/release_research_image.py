"""Build the research image (Dockerfile.research) from a source-only context.

Run on the admin VM (ibkrv2stg-private-admin), in a clean clone of main at the commit to
release, signed in with ``az login``. scripts/research/RELEASE.md has the whole release.

  context            write the build context to .work/research-image/<tree sha256>/context:
                     src/ibkr_etl/**/*.py, scripts/research/*.py, Dockerfile.research,
                     requirements-research.lock and SOURCE_MANIFEST.json, nothing else.
  build [--apply]    build the context with docker on the VM and push it to the registry as
                     ibkr-research:main-<commit>; prints the digest, the Bicep parameters and
                     the two deploy/deployed-images.json entries to record. Without --apply it
                     prints the commands only. --acr builds with ACR Tasks instead (when the
                     VM cannot build).

The build refuses when the lock still lacks hashes, when the clone has uncommitted changes
to the files it ships, or when the in-image check (check_research_image.py) fails.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from ibkr_etl import source_archive  # noqa: E402

REGISTRY = "ibkrv2stgauhecwc4itaoo"
LOGIN_SERVER = f"{REGISTRY}.azurecr.io"
REPOSITORY = "ibkr-research"
DOCKERFILE = "Dockerfile.research"
LOCK = "requirements-research.lock"
JOBS = ("ibkrv2stg-research", "ibkrv2stg-research-idle-stop")
WORK = ROOT / ".work" / "research-image"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def source_names(root: Path = ROOT) -> list[str]:
    names = [p.relative_to(root).as_posix() for p in (root / "src" / "ibkr_etl").rglob("*.py") if "__pycache__" not in p.parts]
    names += [p.relative_to(root).as_posix() for p in (root / "scripts" / "research").glob("*.py")]
    return sorted(names + [DOCKERFILE, LOCK])


def _check(name: str, content: bytes) -> None:
    if PurePosixPath(name).parts[0] in ("src", "scripts"):
        source_archive.check_source(name, content)  # path rules and the secret scan
        return
    text = content.decode("utf-8")
    if "\x00" in text or any(pattern.search(text) for pattern in source_archive.SECRET_PATTERNS):
        raise ValueError(f"{name} needs a private-content review")


def manifest_for(root: Path, names: list[str]) -> tuple[dict, dict[str, bytes]]:
    contents = {}
    for name in names:
        path = root.joinpath(*PurePosixPath(name).parts)
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"Linked or outside source path: {name}")
        data = path.read_bytes()
        _check(name, data)
        contents[name] = data
    entries = [{"path": name, "size": len(data), "sha256": source_archive.sha256(data)} for name, data in contents.items()]
    manifest = {"schema": "wealthryse-source-archive-v1", "image": REPOSITORY, "files": entries,
                "tree_sha256": source_archive.sha256(source_archive.canonical(entries)), "deployment_authorized": False}
    return manifest, contents


def write_context(root: Path = ROOT, work: Path = WORK) -> dict:
    manifest, contents = manifest_for(root, source_names(root))
    context = work / manifest["tree_sha256"] / "context"
    if context.exists():
        shutil.rmtree(context)
    for name, data in contents.items():
        target = context.joinpath(*PurePosixPath(name).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (context / "SOURCE_MANIFEST.json").write_bytes(source_archive.canonical(manifest))
    shipped = {p.relative_to(context).as_posix() for p in context.rglob("*") if p.is_file()}
    if shipped != set(contents) | {"SOURCE_MANIFEST.json"}:
        raise RuntimeError("Unexpected file in the research build context")
    return {"context": str(context), "source_tree_sha256": manifest["tree_sha256"], "files": len(contents)}


def lock_ready(root: Path = ROOT) -> bool:
    text = (root / LOCK).read_text(encoding="utf-8")
    return "hashes missing" not in text and all(
        "--hash=sha256:" in block for block in re.split(r"\n(?=[A-Za-z0-9])", text) if re.match(r"[A-Za-z0-9]", block))


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def build_commands(context: str, commit: str, *, acr: bool) -> list[list[str]]:
    tag = f"{REPOSITORY}:main-{commit}"
    if acr:
        return [["az", "acr", "build", "--registry", REGISTRY, "--image", tag, "--platform", "linux/amd64",
                 "--file", f"{context}/{DOCKERFILE}", context],
                ["az", "acr", "repository", "show", "--name", REGISTRY, "--image", tag, "--query", "digest", "-o", "tsv"]]
    return [["docker", "build", "--pull=false", "--platform", "linux/amd64", "--file", f"{context}/{DOCKERFILE}",
             "--tag", f"{LOGIN_SERVER}/{tag}", context],
            ["az", "acr", "login", "--name", REGISTRY],
            ["docker", "push", f"{LOGIN_SERVER}/{tag}"],
            ["docker", "image", "inspect", "--format", "{{index .RepoDigests 0}}", f"{LOGIN_SERVER}/{tag}"]]


def record_lines(digest: str, commit: str) -> dict:
    image = f"{LOGIN_SERVER}/{REPOSITORY}@{digest}"
    return {"imageDigest": digest, "codeVersion": commit, "deploy/deployed-images.json images": {job: image for job in JOBS}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("context", help="write the source-only build context")
    build = sub.add_parser("build", help="build and push the image (prints the commands without --apply)")
    build.add_argument("--apply", action="store_true")
    build.add_argument("--acr", action="store_true", help="build with ACR Tasks instead of docker on the VM")
    args = parser.parse_args(argv)

    if args.command == "context":
        print("RESEARCH_IMAGE " + json.dumps(write_context(), sort_keys=True))
        return 0
    if not lock_ready():
        raise SystemExit(f"{LOCK} still lacks hashes: run scripts/research/lock_research_requirements.py --fill first")
    shipped = source_names()
    if _git("status", "--porcelain", "--", *shipped):
        raise SystemExit("The clone has uncommitted changes to files the image ships; build from a clean clone of main")
    commit = _git("rev-parse", "--short=7", "HEAD")
    context = write_context()
    commands = build_commands(context["context"], commit, acr=args.acr)
    if not args.apply:
        print("RESEARCH_IMAGE " + json.dumps({"dry_run": True, **context, "commit": commit,
                                              "commands": [" ".join(command) for command in commands]}, sort_keys=True))
        return 0
    output = ""
    for command in commands:
        run = subprocess.run(command, capture_output=True, text=True)
        sys.stderr.write(run.stderr[-4000:])
        if run.returncode:
            raise SystemExit(f"{' '.join(command[:3])} failed; nothing was deployed")
        output = run.stdout.strip()
    found = DIGEST.search(output)
    if not found:
        raise SystemExit(f"No image digest in the last step's output: {output[-300:]!r}")
    print("RESEARCH_IMAGE " + json.dumps({**context, "commit": commit, **record_lines(found.group(0), commit)},
                                         indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
