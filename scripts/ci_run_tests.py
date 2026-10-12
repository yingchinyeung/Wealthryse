"""Run the unit tests for CI and compare failures with tests/known_failures.txt.

The repository was imported with some tests already failing, mostly because
they read private templates or working files that are kept out of git. Those
tests still run on every build; CI fails only when a test outside the list
fails, so new breakage is caught while the old failures are worked down.
Remove a test from the list once it passes.
"""

from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KNOWN = ROOT / "tests" / "known_failures.txt"


def _failing_ids(result: unittest.TestResult) -> set[str]:
    ids = set()
    for test, _ in [*result.errors, *result.failures]:
        ids.add(getattr(test, "test_case", test).id())
    return ids


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-known", action="store_true",
                        help="rewrite tests/known_failures.txt from this run")
    args = parser.parse_args()

    sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT / "tests"))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    failing = _failing_ids(result)

    if args.write_known:
        KNOWN.write_text("".join(f"{test_id}\n" for test_id in sorted(failing)), encoding="utf-8")
        print(f"Wrote {len(failing)} known failures to {KNOWN.relative_to(ROOT)}")
        return 0

    known = {line.strip() for line in KNOWN.read_text(encoding="utf-8").splitlines() if line.strip()}
    new = sorted(failing - known)
    fixed = sorted(known - failing)
    print(f"\n{result.testsRun} tests, {len(failing)} failing, {len(known)} known failures")
    if fixed:
        print("Now passing; remove from tests/known_failures.txt:")
        print("".join(f"  {test_id}\n" for test_id in fixed), end="")
    if new:
        print("New failures:")
        print("".join(f"  {test_id}\n" for test_id in new), end="")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
