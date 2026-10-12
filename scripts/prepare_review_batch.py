from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an Azure review-batch manifest")
    parser.add_argument("--render-manifest", required=True, type=Path)
    parser.add_argument("--container", default="internal-reports")
    parser.add_argument("--prefix", default="review")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--registration-output", type=Path)
    args = parser.parse_args()

    source_bytes = args.render_manifest.read_bytes()
    source = json.loads(source_bytes)
    account = str(source["account"])
    artifacts: list[dict[str, object]] = []
    for item in source["artifacts"]:
        period_end = str(item["period_end"])
        snapshot_path = Path(item["snapshot_path"])
        snapshot_id = snapshot_path.stem
        files = []
        for report_type in ("factsheet", "presentation"):
            details = item[report_type]
            local_path = Path(details["path"])
            digest = _sha256(local_path)
            if digest != details["sha256"]:
                raise RuntimeError(f"Rendered artifact digest changed: {local_path}")
            blob_name = PurePosixPath(
                args.prefix,
                account,
                period_end,
                local_path.name,
            ).as_posix()
            files.append(
                {
                    "report_type": report_type,
                    "local_path": str(local_path.resolve()),
                    "container": args.container,
                    "blob_name": blob_name,
                    "output_reference": f"{args.container}/{blob_name}",
                    "sha256": digest,
                    "bytes": local_path.stat().st_size,
                }
            )
        artifacts.append(
            {
                "account": account,
                "period_end": period_end,
                "snapshot_id": snapshot_id,
                "files": files,
            }
        )

    result = {
        "schema_version": 1,
        "account": account,
        "start": source["start"],
        "end": source["end"],
        "period_count": len(artifacts),
        "render_manifest_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "artifacts": artifacts,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    if args.registration_output:
        registration = json.loads(json.dumps(result))
        for item in registration["artifacts"]:
            for file in item["files"]:
                file.pop("local_path", None)
        args.registration_output.parent.mkdir(parents=True, exist_ok=True)
        args.registration_output.write_text(
            json.dumps(registration, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    print(json.dumps({"status": "complete", "period_count": len(artifacts)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
