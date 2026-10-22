"""Check whether the Metrics guide has fallen behind the website.

    python scripts/check_metrics_guide.py              # report; exit 2 on drift
    python scripts/check_metrics_guide.py --warn       # report as CI warnings; always exit 0
    python scripts/check_metrics_guide.py --brief FILE # also write the drafting brief
    python scripts/check_metrics_guide.py --refresh    # record today's calculations as checked

Reads source files only. See src/ibkr_etl/metrics_guide_audit.py.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ibkr_etl import metrics_guide_audit as audit_module  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh", action="store_true", help="record the current calculation fingerprints")
    parser.add_argument("--warn", action="store_true", help="print GitHub warnings and exit 0")
    parser.add_argument("--brief", type=Path, help="write the drafting brief (Markdown) to this file")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    if args.refresh:
        report = audit_module.audit()
        if report.uncovered_labels or report.missing_sources:
            print("Fix the labels and watch list before refreshing:")
            print(json.dumps(report.as_dict(), indent=2, ensure_ascii=False))
            return 1
        sources = audit_module.write_manifest()
        print(f"Recorded {len(sources)} calculation fingerprints in {audit_module.MANIFEST.relative_to(ROOT)}")
        return 0

    report = audit_module.audit()
    if args.brief:
        args.brief.write_text(audit_module.brief(report), encoding="utf-8")
    if args.json:
        print(json.dumps(report.as_dict(), indent=2, ensure_ascii=False))
    elif report.clean:
        print("Metrics guide is up to date with the website.")
    else:
        prefix = "::warning title=Metrics guide::" if args.warn else "- "
        for label, path in report.uncovered_labels.items():
            print(f"{prefix}'{label}' in {path} has no definition")
        for source, metrics in report.changed_sources.items():
            print(f"{prefix}{source} changed; re-read: {', '.join(metrics)}")
        for source in report.unrecorded_sources:
            print(f"{prefix}{source} is watched but not recorded; run with --refresh")
        for source in report.missing_sources:
            print(f"{prefix}{source} no longer exists; update WATCHED_SOURCES")
    if args.warn:
        return 0
    return 0 if report.clean else 2


if __name__ == "__main__":
    raise SystemExit(main())
