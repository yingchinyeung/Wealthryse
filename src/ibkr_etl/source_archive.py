"""Content-addressed source backups, never deployment authorization.

Only explicitly selected UTF-8 source/config files may enter an archive. Private
templates and renderer binaries require a separate reviewed build-input channel.
The lightweight checks here supplement (not replace) a production secret scanner.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Iterable

ROOT_FILES = frozenset({"Dockerfile", "Dockerfile.azure", "Dockerfile.risk", "Dockerfile.risk.minimal", ".dockerignore", ".gitignore",
                        "pyproject.toml", "requirements.lock", "uv.lock"})
SOURCE_TYPES = {"src": {".py"}, "tests": {".py"},
                "scripts": {".py", ".mjs", ".ps1"}, "infra": {".bicep"},
                "migrations": {".sql"}, "queries": {".sql"}}
FORBIDDEN = frozenset({"node_modules", "__pycache__", "private-inputs", "output", "raw",
                       "reports", "backups", ".work", ".git", ".venv"})
MAX_FILE = 8 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
MAX_FILES = 2000
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)(?:postgres(?:ql)?|https?)://[^\s/:\"'<>]+:[^\s/@\"'<>]+@"),
    re.compile(r"(?i)AccountKey=[A-Za-z0-9+/]{40,}={0,2}"),
    re.compile(r"(?im)^\s*(?:FLEX_TOKEN|DATABASE_PASSWORD|CLIENT_SECRET|API_KEY)\s*=\s*['\"][A-Za-z0-9_+/=-]{16,}['\"]"),
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def allowed_path(name: str) -> bool:
    path = PurePosixPath(name)
    if (not name or "\\" in name or ":" in name or path.is_absolute()
            or path.as_posix() != name or any(p in {".", ".."} for p in path.parts)
            or any(p.lower() in FORBIDDEN for p in path.parts)):
        return False
    return name in ROOT_FILES or (len(path.parts) > 1
        and path.suffix in SOURCE_TYPES.get(path.parts[0], set()))


def check_source(name: str, content: bytes) -> None:
    if not allowed_path(name) or len(content) > MAX_FILE:
        raise ValueError(f"Disallowed source path or size: {name}")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"Non-text source: {name}") from None
    # Reviewed Bicep expressions reference secure parameters; they are not
    # embedded passwords. Do not exempt an entire file or arbitrary URL.
    scanned = text
    if name.endswith(".bicep"):
        scanned = re.sub(r"postgresql:" r"//\$\{[A-Za-z][A-Za-z0-9_]*\}:\$\{uriComponent\([A-Za-z][A-Za-z0-9_]*\)\}@",
                         "postgresql://PARAMETER_REFERENCE@", scanned)
    if name.startswith("tests/"):
        scanned = scanned.replace('flex_token="not-a-real-token"', 'flex_token=TEST_PLACEHOLDER')
    if "\x00" in text or any(p.search(scanned) for p in SECRET_PATTERNS):
        raise ValueError(f"Source needs private-content review: {name}")


def build_archive(root: Path, names: Iterable[str]) -> tuple[bytes, dict]:
    root = root.resolve(strict=True)
    selected = sorted(set(names))
    if not selected or len(selected) > MAX_FILES:
        raise ValueError("Invalid source inventory size")
    contents = {}
    total = 0
    for name in selected:
        if not allowed_path(name):
            raise ValueError(f"Disallowed source path: {name}")
        path = root.joinpath(*PurePosixPath(name).parts)
        # Do not follow symlinks/junctions, including a linked ancestor directory.
        for part in [path, *path.parents]:
            if part == root:
                break
            if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
                raise ValueError(f"Linked source path: {name}")
        if not path.resolve(strict=True).is_relative_to(root):
            raise ValueError(f"Source escaped root: {name}")
        if path.stat().st_size > MAX_FILE:
            raise ValueError(f"Oversized source: {name}")
        content = path.read_bytes()
        check_source(name, content)
        total += len(content)
        if total > MAX_TOTAL:
            raise ValueError("Source inventory too large")
        contents[name] = content
    entries = [{"path": name, "size": len(data), "sha256": sha256(data)}
               for name, data in contents.items()]
    manifest = {"schema": "wealthryse-source-archive-v1", "files": entries,
                "tree_sha256": sha256(canonical(entries)), "deployment_authorized": False}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in [*contents.items(), ("SOURCE_MANIFEST.json", canonical(manifest))]:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, content)
    data = stream.getvalue()
    verify_archive(data, sha256(data))
    return data, manifest


def verify_archive(data: bytes, expected_sha256: str) -> dict:
    """Verify without extracting or executing anything; digest must be trusted separately."""
    if len(data) > MAX_TOTAL or sha256(data) != expected_sha256:
        raise ValueError("Source archive digest or size mismatch")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        items = archive.infolist()
        names = [item.filename for item in items]
        if (len(names) != len(set(names)) or len(names) > MAX_FILES + 1
                or names.count("SOURCE_MANIFEST.json") != 1
                or sum(item.file_size for item in items) > MAX_TOTAL + MAX_FILE):
            raise ValueError("Invalid archive inventory")
        for item in items:
            if (item.file_size > MAX_FILE or item.flag_bits & 1
                    or stat.S_ISLNK(item.external_attr >> 16)
                    or (item.filename != "SOURCE_MANIFEST.json" and not allowed_path(item.filename))):
                raise ValueError("Invalid archive member")
        manifest = json.loads(archive.read("SOURCE_MANIFEST.json"))
        if (manifest.get("schema") != "wealthryse-source-archive-v1"
                or manifest.get("deployment_authorized") is not False):
            raise ValueError("Invalid source manifest")
        actual = []
        for name in sorted(n for n in names if n != "SOURCE_MANIFEST.json"):
            content = archive.read(name)
            check_source(name, content)
            actual.append({"path": name, "size": len(content), "sha256": sha256(content)})
        if (not actual or manifest.get("files") != actual
                or manifest.get("tree_sha256") != sha256(canonical(actual))):
            raise ValueError("Source inventory content mismatch")
        return manifest
