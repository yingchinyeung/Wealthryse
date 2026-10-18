"""Offline smoke check run inside the research image as its last build step (Dockerfile.research).

No credentials or network. It fails the build unless the image runs non-root, has numpy,
scipy and pyarrow giving known answers, has the job's Azure and Postgres packages, holds
no private documents or report runtime, and every source file matches SOURCE_MANIFEST.json.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NOT_COPIED = {"Dockerfile.research"}  # in the manifest, not in the image


def _job_module():
    spec = importlib.util.spec_from_file_location("run_research_job", ROOT / "scripts" / "research" / "run_research_job.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    if os.getuid() == 0:
        raise RuntimeError("The research image must run non-root")
    for folder in ("scripts/node_modules", "templates", "private-inputs", "migrations"):
        if (ROOT / folder).exists():
            raise RuntimeError(f"The research image must not contain {folder}")

    import numpy
    import pyarrow
    import scipy
    from scipy import stats

    solved = numpy.linalg.solve(numpy.array([[2.0, 1.0], [1.0, 3.0]]), numpy.array([3.0, 5.0]))
    if not numpy.allclose(solved, [0.8, 1.4]):
        raise RuntimeError(f"numpy answered {solved}")
    if not math.isclose(float(stats.norm.cdf(1.959963984540054)), 0.975, rel_tol=1e-12):
        raise RuntimeError("scipy.stats.norm.cdf gave a wrong answer")

    sys.path.insert(0, str(ROOT / "src"))
    from ibkr_etl.research import feature_store

    rows = [{"symbol": "AAA", "price_date": "2026-01-02", "close": 101.25, "known_at": "2026-01-02T21:00:00+00:00"}]
    columns = ["symbol", "price_date", "close", "known_at"]
    if feature_store.read_parquet(feature_store.parquet_bytes(rows, columns)) != rows:
        raise RuntimeError("A Parquet round trip changed the rows")

    import azure.identity  # noqa: F401
    import azure.storage.blob  # noqa: F401
    import psycopg  # noqa: F401
    from zoneinfo import ZoneInfo

    ZoneInfo("America/New_York")
    _job_module().build_parser().parse_args(["backtest", "--spec", "spec.json"])

    manifest = json.loads((ROOT / "SOURCE_MANIFEST.json").read_text(encoding="utf-8"))
    checked = 0
    for entry in manifest["files"]:
        if entry["path"] in NOT_COPIED:
            continue
        if hashlib.sha256((ROOT / entry["path"]).read_bytes()).hexdigest() != entry["sha256"]:
            raise RuntimeError(f"{entry['path']} differs from the source manifest")
        checked += 1
    print("RESEARCH_IMAGE " + json.dumps({
        "status": "pass", "non_root": True, "numpy": numpy.__version__, "scipy": scipy.__version__,
        "pyarrow": pyarrow.__version__, "files_checked": checked, "source_tree_sha256": manifest["tree_sha256"],
    }, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
