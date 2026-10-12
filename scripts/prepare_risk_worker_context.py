"""Create an allowlisted source-only ACR build context; never use repo-wide upload."""
import io
import json
from pathlib import Path
import zipfile
from ibkr_etl.source_archive import build_archive, sha256

ROOT = Path(__file__).resolve().parents[1]


def main():
    names = [p.relative_to(ROOT).as_posix() for p in (ROOT / "src").rglob("*.py")]
    names += ["Dockerfile.risk", "requirements.lock", "scripts/build_risk_snapshots.py",
              "scripts/check_risk_runtime.py", "scripts/audit_risk_cache.py"]
    archive, manifest = build_archive(ROOT, names)
    folder = ROOT / ".work/risk-worker-release" / manifest["tree_sha256"]
    context = folder / "context"
    context.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(archive)) as checked:
        for name in checked.namelist():
            # Every member was path/content validated by build_archive.
            path = context / name
            data = checked.read(name)
            if path.exists() and path.read_bytes() != data:
                raise RuntimeError("Immutable build context differs")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    if {p.relative_to(context).as_posix() for p in context.rglob("*") if p.is_file()} != set(names) | {"SOURCE_MANIFEST.json"}:
        raise RuntimeError("Unexpected file in source-only risk context")
    (folder / "source.zip").write_bytes(archive)
    print(json.dumps({"context":str(context), "source_tree_sha256":manifest["tree_sha256"],
                      "archive_sha256":sha256(archive), "files":len(names), "private_documents":0, "renderer_files":0}))


if __name__ == "__main__":
    main()
