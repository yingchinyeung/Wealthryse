"""Local build-input inventory; does not copy assets or grant licence rights."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def fingerprint_file(root: Path, relative: str) -> dict:
    root = root.resolve(strict=True)
    logical = PurePosixPath(relative)
    if (logical.is_absolute() or ".." in logical.parts or "\\" in relative
            or ":" in relative or logical.as_posix() != relative):
        raise ValueError("Unsafe build-input path")
    path = root.joinpath(*logical.parts)
    for part in [path, *path.parents]:
        if part == root:
            break
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ValueError("Linked build input is not accepted")
    if not path.resolve(strict=True).is_relative_to(root) or not path.is_file():
        raise ValueError("Build input must be a regular file inside the project")
    digest = hashlib.sha256()
    size = 0
    before = path.stat()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or size != after.st_size:
        raise ValueError("Build input changed during inventory")
    return {"path": relative, "size": size, "sha256": digest.hexdigest()}


def fingerprint_tree(root: Path, relative: str) -> dict:
    root = root.resolve(strict=True)
    logical = PurePosixPath(relative)
    if (logical.is_absolute() or ".." in logical.parts or "\\" in relative
            or ":" in relative or logical.as_posix() != relative):
        raise ValueError("Unsafe runtime directory path")
    folder = root / relative
    if (not folder.is_dir() or not folder.resolve(strict=True).is_relative_to(root)
            or folder.is_symlink() or (hasattr(folder, "is_junction") and folder.is_junction())):
        raise ValueError("Unsafe runtime directory")
    files = []
    for parent, dirs, names in os.walk(folder, followlinks=False):
        for name in dirs:
            child = Path(parent) / name
            if child.is_symlink() or (hasattr(child, "is_junction") and child.is_junction()):
                raise ValueError("Linked runtime directory is not accepted")
        for name in sorted(names):
            files.append(fingerprint_file(root, (Path(parent) / name).relative_to(root).as_posix()))
    if not files:
        raise ValueError("Empty runtime directory")
    files.sort(key=lambda entry: entry["path"])
    return {"path": relative, "files": files, "file_count": len(files),
            "total_bytes": sum(entry["size"] for entry in files),
            "tree_sha256": hashlib.sha256(canonical(files)).hexdigest()}


def classify_bundled_licence(text: str) -> dict:
    """Conservative triage, not legal advice or an automated licence approval.

    Any alternative/granted production licence requires a separate authoritative
    rights review. Unrecognized wording never becomes a pass by default.
    """
    normalized = " ".join(text.lower().split())
    restricted = ("solely for internal evaluation and testing purposes" in normalized
                  and "production, commercial, or benchmarking" in normalized)
    return {"status": "production_restricted" if restricted else "review_required",
            "production_authorized": False,
            "reason": "Written production permission or replacement required" if restricted
                      else "Production rights have not been independently established"}


def require_production_inputs(manifest: dict) -> None:
    """Inventory-only records cannot be promoted by setting a ready flag."""
    if manifest.get("schema") != "wealthryse-build-input-inventory-v1":
        raise ValueError("Unknown build-input inventory")
    if manifest.get("runtime_licence", {}).get("status") == "production_restricted":
        raise ValueError("Presentation runtime has restricted production rights")
    # This stage intentionally has no owner-click override or external licence
    # approval path. The future release controller must verify authoritative
    # rights evidence, not trust fields supplied in a client manifest.
    raise ValueError("Build inputs still require an independent production-rights review")
