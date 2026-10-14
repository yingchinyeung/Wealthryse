"""Stage 3 reports for new-onboarding composites in the Alpha World MS or T design.

The month-end report job runs this after its packages, in the same image and
with the same private storage and approved deck template. It does nothing until
migration 064 exists, or while no composite case is waiting on its reports.
Each case's outcome is recorded on its reports_rendered check; see
ibkr_etl/composite_reports.py.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from azure.core.exceptions import ResourceExistsError  # noqa: E402

from ibkr_etl.cloud_storage import download_blob_bytes, upload_bytes  # noqa: E402
from ibkr_etl.composite_presentation import CONTRACT_VERSION  # noqa: E402
from ibkr_etl.composite_reports import HONG_KONG, DeckRenderer, run_composite_report_cases  # noqa: E402
from ibkr_etl.db import connect  # noqa: E402
from validate_subject_presentation import validate_subject_presentation  # noqa: E402


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def deck_renderer() -> DeckRenderer:
    """The approved v24 deck template, as the month-end package uses it, and the composite adapter."""

    template = Path(_required("PRESENTATION_TEMPLATE_PATH"))
    template_sha = _required("PRESENTATION_TEMPLATE_SHA256").lower()
    if not template.is_file() or hashlib.sha256(template.read_bytes()).hexdigest() != template_sha:
        raise RuntimeError("The approved presentation template is missing or changed")

    def render(snapshot_text: str, output: Path) -> dict:
        payload = json.loads(snapshot_text)
        strategy = "iStrategy: " + str(payload["report_profile"]["display_name"]).strip()
        with TemporaryDirectory(prefix="composite-deck-") as directory:
            snapshot_file = Path(directory) / "snapshot.json"
            snapshot_file.write_text(snapshot_text, encoding="utf-8")
            try:
                completed = subprocess.run(
                    [os.environ.get("NODE_EXECUTABLE", "node"),
                     str(PROJECT_ROOT / "scripts" / "render_composite_presentation.mjs"),
                     str(template), str(snapshot_file), str(output)],
                    capture_output=True, text=True, cwd=PROJECT_ROOT,
                    env={**os.environ, "PRESENTATION_STRATEGY_NAME": strategy}, timeout=600,
                )
            except subprocess.TimeoutExpired:
                raise RuntimeError("The deck renderer did not finish within 600 seconds") from None
            sys.stderr.write(completed.stdout + completed.stderr)  # the job's log keeps the renderer's output
            if completed.returncode:
                # The case page shows the renderer's own reason, without this run's temporary paths,
                # so an unchanged failure is not recorded again every day.
                lines = [line.strip() for line in completed.stderr.splitlines() if line.strip()]
                reason = next((found.group(1) for line in lines if (found := re.match(r"\w*Error(?: \[\w+\])?: (.+)", line))),
                              lines[0] if lines else "no reason given")
                for path, label in ((directory, "<snapshot>"), (str(output.parent), "<output>"),
                                    (str(PROJECT_ROOT), "<app>")):
                    reason = reason.replace(path, label)
                raise RuntimeError(f"The deck renderer failed: {reason[:300]}")
            return validate_subject_presentation(output, snapshot_file, subject_id=str(payload["subject_id"]),
                                                 period_end=str(payload["period_end"]), expected_strategy=strategy)

    return DeckRenderer(f"sha256:{template_sha}+layout:{CONTRACT_VERSION}", render)


def main() -> int:
    if os.environ.get("DEPLOYMENT_ENVIRONMENT", "").lower() != "internal-production":
        raise RuntimeError("Onboarding composite reports run only in internal production")
    database_url = _required("DATABASE_URL")
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("DATABASE_URL must be a PostgreSQL connection URL")
    tenant_id = os.environ.get("PORTFOLIO_TENANT_ID", "00000000-0000-0000-0000-000000000001")
    storage_url = _required("AZURE_STORAGE_ACCOUNT_URL")
    container = os.environ.get("INTERNAL_REPORT_CONTAINER", "internal-reports")

    def fetch(bucket: str, blob: str) -> bytes:
        return download_blob_bytes(storage_url, bucket, blob, maximum_bytes=64 * 1024 * 1024)

    def store(bucket: str, blob: str, content: bytes, content_type: str) -> None:
        # Content-addressed and immutable: an existing blob must already hold these bytes.
        try:
            stored = upload_bytes(storage_url, bucket, blob, content, overwrite=False, content_type=content_type)
        except ResourceExistsError:
            stored = fetch(bucket, blob) == content
        if not stored:
            raise RuntimeError("Private report storage refused the report")

    connection = connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id)
    try:
        if connection.execute("SELECT to_regclass('public.broker_onboarding_cases')").fetchone()[0] is None:
            print(json.dumps({"status": "not_installed", "cases": []}, sort_keys=True))
            return 0
        results = run_composite_report_cases(connection, tenant_id, container=container,
                                             today=datetime.now(HONG_KONG).date(), fetch=fetch, store=store,
                                             deck=deck_renderer())
    finally:
        connection.close()
    print(json.dumps({"status": "success", "cases": results}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
