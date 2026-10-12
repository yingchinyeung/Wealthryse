"""Prepare source-only backup. Does not commit Git, upload, build or deploy."""
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ibkr_etl.source_archive import allowed_path, build_archive, sha256

ROOT = Path(__file__).resolve().parents[1]


def main():
    inventory = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT
    ).decode().split("\0")
    names = sorted({name for name in inventory if allowed_path(name) and (ROOT / name).is_file()})
    archive, manifest = build_archive(ROOT, names)
    digest = sha256(archive)
    folder = ROOT / ".work" / "azure-native-release" / digest
    folder.mkdir(parents=True, exist_ok=True)
    archive_path = folder / "source.zip"
    if archive_path.exists() and archive_path.read_bytes() != archive:
        raise RuntimeError("Refusing to overwrite different source archive")
    archive_path.write_bytes(archive)
    record = {"schema": "wealthryse-source-record-v1", "state": "prepared_not_approved",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_archive_sha256": digest, "source_tree_sha256": manifest["tree_sha256"],
        "source_file_count": len(names), "archive_bytes": len(archive),
        "git_base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip(),
        "git_worktree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)),
        "deployment_authorized": False, "build_ready": False,
        "outstanding_gates": ["independent_secret_scan", "private_build_inputs", "build_and_security_scans",
            "staging_acceptance", "owner_approval", "restricted_deployment_identity"],
        "exclusions": ["private templates", "renderer binaries", "operational documents", "statements",
                       "generated reports", "environment files", "local evidence", "Git history"]}
    record_path = folder / "source-record.json"
    if not record_path.exists():
        record_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps({"archive": str(archive_path), "record": str(record_path),
                      "sha256": digest, "files": len(names), "bytes": len(archive)}))


if __name__ == "__main__":
    main()
