from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PATH_PARTS = {
    "private-inputs",
    "output",
    "raw",
    "reports",
    "backups",
    "tmp",
    ".work",
    "node_modules",
}
SECRET_PATTERNS = (
    re.compile(
        r"(?m)^\s*(?:FLEX_TOKEN|DATABASE_PASSWORD|CLIENT_SECRET|API_KEY)"
        r"\s*=\s*['\"][^'\"]{12,}['\"]"
    ),
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
)
SECRET_SCAN_EXCLUSIONS = {
    ".env.example",
    ".env.docker.example",
    "tests/test_postgres_adapter.py",
}


def tracked_files() -> list[Path]:
    output = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=ROOT
    ).decode("utf-8")
    return [Path(value) for value in output.split("\0") if value]


def source_files() -> list[Path]:
    """Also inspect new non-ignored source before it has been committed."""
    output = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT
    ).decode("utf-8")
    return sorted({Path(value) for value in output.split("\0") if value})


def main() -> int:
    tracked = tracked_files()
    unsafe = [
        path.as_posix()
        for path in tracked
        if any(part in FORBIDDEN_PATH_PARTS for part in path.parts)
    ]
    if unsafe:
        raise RuntimeError("Generated or private paths are tracked: " + ", ".join(unsafe))

    secret_hits: list[str] = []
    scanned = source_files()
    for relative in scanned:
        normalized = relative.as_posix()
        if normalized in SECRET_SCAN_EXCLUSIONS:
            continue
        path = ROOT / relative
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            secret_hits.append(normalized)
    if secret_hits:
        raise RuntimeError("Potential credential material is tracked: " + ", ".join(secret_hits))

    migrations = sorted((ROOT / "migrations" / "postgres").glob("[0-9][0-9][0-9]_*.sql"))
    numbers = [int(path.name[:3]) for path in migrations]
    expected = list(range(1, max(numbers, default=0) + 1))
    if numbers != expected:
        raise RuntimeError(f"PostgreSQL migrations are not contiguous: {numbers}")

    for relative in (
        Path("scripts/dispatch_institutional_daily_work.py"),
        Path("scripts/dispatch_month_end_work.py"),
        Path("scripts/reconcile_institutional_shadow.py"),
        Path("src/ibkr_etl/operating_registry.py"),
        Path("src/ibkr_etl/shadow_reconciliation.py"),
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        if re.search(r"U\d{7,}", text):
            raise RuntimeError(f"Generic orchestration embeds an account ID: {relative}")

    print(
        f"PASS: {len(tracked)} tracked files, {len(scanned)} source files scanned, {len(migrations)} contiguous migrations, no prohibited paths or secret patterns"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
