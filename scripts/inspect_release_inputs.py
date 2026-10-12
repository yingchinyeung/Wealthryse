"""Hash private build inputs locally without uploading or executing the runtime."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from ibkr_etl.build_inputs import canonical, classify_bundled_licence, fingerprint_file, fingerprint_tree

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = "scripts/node_modules/@oai/artifact-tool"
TEMPLATES = (
    "output/pptx/U16088571_Monthly_Presentation_2026-08-31_Enhanced_v24.pptx",
    "output/pdf/U16088571_Factsheet_2026-08-31_Approved_v6.pdf",
    "private-inputs/U16088571/monthly factsheet template.pdf",
    "private-inputs/U16088606/monthly factsheet template.pdf",
    "private-inputs/U16088606/monthly presentation deck template.pptx",
    "private-inputs/U16088606/previously completed factsheet.pdf",
    "private-inputs/U16088606/template-intake-manifest.json",
)


def main():
    # The existing node_modules is a junction to the desktop runtime. Resolve
    # this ONE known input explicitly for read-only inventory. Do not package
    # or follow other linked directories discovered inside it.
    runtime_root = (ROOT / RUNTIME).resolve(strict=True)
    licence = fingerprint_file(runtime_root, "LICENSE.md")
    raw = (runtime_root / "LICENSE.md").read_bytes()
    if hashlib.sha256(raw).hexdigest() != licence["sha256"]:
        raise RuntimeError("Licence changed during inspection")
    text = raw.decode("utf-8")
    result = {"schema": "wealthryse-build-input-inventory-v1",
              "inspected_at": datetime.now(timezone.utc).isoformat(),
              "templates": [fingerprint_file(ROOT, name) for name in TEMPLATES],
              "runtime": fingerprint_tree(runtime_root.parent, runtime_root.name),
              "runtime_source": {"configured_path": RUNTIME, "resolved_path": str(runtime_root),
                                 "packaging_authorized": False},
              "runtime_licence": {**classify_bundled_licence(text), "evidence": licence},
              "production_build_ready": False, "deployment_authorized": False,
              "scope": "local bytes only; not a Linux build/runtime or licence acceptance"}
    data = canonical(result)
    digest = hashlib.sha256(data).hexdigest()
    folder = ROOT / ".work" / "azure-native-release" / "input-inventories"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (digest + ".json")
    path.write_bytes(data)
    print(json.dumps({"inventory": str(path), "sha256": digest,
        "template_count": len(result["templates"]), "runtime_files": result["runtime"]["file_count"],
        "runtime_bytes": result["runtime"]["total_bytes"],
        "runtime_licence_status": result["runtime_licence"]["status"], "production_build_ready": False}))


if __name__ == "__main__":
    main()
