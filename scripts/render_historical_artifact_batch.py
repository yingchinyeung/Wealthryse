from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from ibkr_etl.monthly_artifacts import (
    FACTSHEET_LAYOUT_VERSION,
    load_snapshot,
    render_factsheet,
)
from pypdf import PdfReader


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render a validated month-end PDF/PPTX batch from frozen snapshots."
    )
    parser.add_argument("--snapshot-root", required=True, type=Path)
    parser.add_argument("--account", default="U16088571")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--factsheet-template", required=True, type=Path)
    parser.add_argument("--presentation-template", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--node", required=True, type=Path)
    parser.add_argument("--presentation-renderer", required=True, type=Path)
    parser.add_argument("--skill-dir", required=True, type=Path)
    parser.add_argument("--runtime-python", required=True, type=Path)
    parser.add_argument("--runtime-node-modules", required=True, type=Path)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--reuse-existing",
        action="store_true",
        help="Validate existing PDF/PPTX files and rebuild only the batch manifest.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.overwrite and args.reuse_existing:
        raise ValueError("--overwrite and --reuse-existing are mutually exclusive")
    account_root = args.snapshot_root / args.account
    if not account_root.is_dir():
        raise FileNotFoundError(f"Snapshot account directory not found: {account_root}")
    for required in (
        args.factsheet_template,
        args.presentation_template,
        args.node,
        args.presentation_renderer,
        args.skill_dir,
        args.runtime_python,
        args.runtime_node_modules,
    ):
        if not required.exists():
            raise FileNotFoundError(required)

    periods = sorted(
        directory.name
        for directory in account_root.iterdir()
        if directory.is_dir() and args.start <= directory.name <= args.end
    )
    if not periods:
        raise ValueError("No snapshot periods were found in the requested range")

    args.output_root.mkdir(parents=True, exist_ok=True)
    manifest_entries: list[dict[str, object]] = []
    child_environment = os.environ.copy()
    child_environment.update(
        {
            "SKILL_DIR": str(args.skill_dir.resolve()),
            "RUNTIME_PYTHON": str(args.runtime_python.resolve()),
            "RUNTIME_NODE_MODULES": str(args.runtime_node_modules.resolve()),
        }
    )

    for period in periods:
        candidates = sorted((account_root / period).glob("*.json"))
        if len(candidates) != 1:
            raise ValueError(
                f"Expected exactly one frozen snapshot for {period}; found {len(candidates)}"
            )
        snapshot_path = candidates[0]
        snapshot = load_snapshot(snapshot_path)
        if snapshot.get("period_end") != period:
            raise ValueError(f"Snapshot period mismatch for {snapshot_path}")

        period_dir = args.output_root / period
        period_dir.mkdir(parents=True, exist_ok=True)
        factsheet_path = period_dir / f"{args.account}_Factsheet_{period}.pdf"
        presentation_path = period_dir / f"{args.account}_Monthly_Presentation_{period}.pptx"
        if args.reuse_existing:
            for artifact_path in (factsheet_path, presentation_path):
                if not artifact_path.is_file():
                    raise FileNotFoundError(artifact_path)
            pages = len(PdfReader(factsheet_path).pages)
            if pages != 2:
                raise ValueError(f"Expected two factsheet pages for {period}; found {pages}")
            subprocess.run(
                [
                    str(args.runtime_python),
                    str(PROJECT_ROOT / "scripts" / "validate_monthly_presentation.py"),
                    str(presentation_path),
                    "--expected-period",
                    period,
                ],
                check=True,
                env=child_environment,
            )
            factsheet_result = {
                "pages": pages,
                "layout_version": FACTSHEET_LAYOUT_VERSION,
            }
        else:
            for artifact_path in (factsheet_path, presentation_path):
                if artifact_path.exists():
                    if not args.overwrite:
                        raise FileExistsError(artifact_path)
                    artifact_path.unlink()

            factsheet_result = render_factsheet(
                snapshot,
                template_path=args.factsheet_template,
                output_path=factsheet_path,
            )
            subprocess.run(
                [
                    str(args.node),
                    str(args.presentation_renderer),
                    str(args.presentation_template),
                    str(snapshot_path),
                    str(presentation_path),
                ],
                check=True,
                env=child_environment,
            )
        manifest_entries.append(
            {
                "account": args.account,
                "period_end": period,
                "snapshot_path": str(snapshot_path.resolve()),
                "snapshot_sha256": _sha256(snapshot_path),
                "factsheet": {
                    "path": str(factsheet_path.resolve()),
                    "sha256": _sha256(factsheet_path),
                    "bytes": factsheet_path.stat().st_size,
                    "pages": factsheet_result["pages"],
                    "layout_version": factsheet_result["layout_version"],
                },
                "presentation": {
                    "path": str(presentation_path.resolve()),
                    "sha256": _sha256(presentation_path),
                    "bytes": presentation_path.stat().st_size,
                    "slides": 20,
                },
            }
        )
        print(
            json.dumps(
                {
                    "status": "validated_existing" if args.reuse_existing else "rendered",
                    "period_end": period,
                }
            ),
            flush=True,
        )

    manifest_path = args.output_root / "manifest.json"
    temporary_manifest = manifest_path.with_suffix(".json.tmp")
    temporary_manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "account": args.account,
                "start": args.start,
                "end": args.end,
                "period_count": len(manifest_entries),
                "artifacts": manifest_entries,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    shutil.move(temporary_manifest, manifest_path)
    print(
        json.dumps(
            {
                "status": "complete",
                "period_count": len(manifest_entries),
                "manifest": str(manifest_path.resolve()),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
