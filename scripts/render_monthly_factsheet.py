from __future__ import annotations

import argparse
import json

from ibkr_etl.monthly_artifacts import load_snapshot, render_factsheet


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a U16088571 monthly factsheet")
    parser.add_argument("--template", required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = render_factsheet(
        load_snapshot(args.snapshot),
        template_path=args.template,
        output_path=args.output,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
