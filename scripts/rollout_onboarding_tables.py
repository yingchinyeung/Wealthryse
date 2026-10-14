"""The new account onboarding's tables: migrations 064 and 065 and the website's grants.

Run on the owner's computer, from a clone of main, after `az login`, with a
Python that has requests and azure-identity:

    python scripts/rollout_onboarding_tables.py check
    python scripts/rollout_onboarding_tables.py start
    python scripts/rollout_onboarding_tables.py result --execution ibkrv2stg-bootstrap-xxxxxxx

check is read-only. start runs one execution of the existing ibkrv2stg-bootstrap
job with an override that runs onboarding_tables_payload.py. No job, image,
identity, secret or setting is changed, and no image is built; the website stays
as it is (ONBOARDING_V2_ENABLED is a separate step). result reads the execution's
one result line from Log Analytics; it can take a few minutes to arrive.

Same mechanics as scripts/rollout_live_data_stage1.py, whose Azure helpers this uses.
"""
from __future__ import annotations

import argparse
import base64
import copy
import gzip
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rollout_live_data_stage1 as stage1  # noqa: E402

ROOT = stage1.ROOT
JOB, JOB_PATH, API = stage1.JOB, stage1.JOB_PATH, stage1.API
TARGETS = ("064_broker_connections_and_onboarding_cases.sql", "065_onboarding_report_style.sql")
# 063 belongs to the live data rollout (rollout_live_data_stage1.py); 064 and 065 do not depend on it.
OTHER_ROLLOUTS = (stage1.TARGET,)
WEB_ROLE = "ibkr_stg_web"
APPROVAL_ROLE = "ibkr_stg_approval"
PREFIX = "ONBOARDING_TABLES "
PAYLOAD = "scripts/onboarding_tables_payload.py"


def bundle(commit: str = "HEAD") -> dict:
    names = subprocess.run(["git", "ls-tree", "--name-only", commit, "migrations/postgres/"], cwd=ROOT,
                           capture_output=True, text=True, check=True).stdout.split()
    texts = {Path(name).name: stage1.git_text(name, commit) for name in names if name.endswith(".sql")}
    if not set(TARGETS) <= set(texts):
        raise SystemExit("Migrations 064 and 065 are not in " + commit + "; run this from main")
    return {
        "expected": {name: hashlib.sha256(text.encode("utf-8")).hexdigest() for name, text in sorted(texts.items())},
        "held_back": list(stage1.HELD_BACK),
        "other_rollouts": list(OTHER_ROLLOUTS),
        "targets": list(TARGETS),
        "target_sql": {name: texts[name] for name in TARGETS},
        "web_role": WEB_ROLE,
        "approval_role": APPROVAL_ROLE,
        "database": stage1.DATABASE,
    }


def payload_sha256(value: dict, commit: str = "HEAD") -> str:
    unpacked = stage1.git_text(PAYLOAD, commit) + "\n" + json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(unpacked.encode()).hexdigest()


def command(value: dict, commit: str = "HEAD") -> list[str]:
    source = stage1.git_text(PAYLOAD, commit)
    marker = "BUNDLE = None  # replaced by rollout_onboarding_tables.py"
    if source.count(marker) != 1:
        raise SystemExit("Payload placeholder missing")
    source = source.replace(marker, "BUNDLE = " + repr(stage1.encode(value))) + "\nraise SystemExit(main())\n"
    packed = base64.b64encode(gzip.compress(source.encode(), 9, mtime=0)).decode()
    return ["-c", "import base64,gzip;exec(compile(gzip.decompress(base64.b64decode('" + packed
            + "')),'onboarding-tables','exec'))"]


def template(container: dict, args: list[str]) -> dict:
    value = copy.deepcopy(container)
    value.update(command=["python"], args=args, resources={"cpu": 0.5, "memory": "1Gi"},
                 env=[{"name": "DATABASE_ADMIN_URL", "secretRef": "database-admin-url"}])
    for key in ("volumeMounts", "probes"):
        value.pop(key, None)
    return {"containers": [value]}


def preflight() -> dict:
    if stage1.request("GET", f"/subscriptions/{stage1.SUB}?api-version=2022-12-01").get("state") != "Enabled":
        raise SystemExit("The subscription is not enabled for this login")
    job = stage1.request("GET", JOB_PATH + API)
    container = stage1.validate_job(job, stage1.recorded_image())
    runs = stage1.request("GET", JOB_PATH + "/executions" + API).get("value", [])
    busy = [r["name"] for r in runs if r["properties"]["status"] not in ("Succeeded", "Failed", "Stopped")]
    if busy:
        raise SystemExit("The bootstrap job is busy: " + ", ".join(busy))
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    return {"container": container, "bundle": bundle(), "head": head}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", choices=["check", "start", "result"])
    parser.add_argument("--execution")
    args = parser.parse_args(argv)

    if args.stage == "result":
        if not re.fullmatch(JOB + r"-[a-z0-9]{7}", args.execution or ""):
            raise SystemExit("--execution must name an ibkrv2stg-bootstrap execution")
        run = stage1.request("GET", JOB_PATH + "/executions/" + args.execution + API)
        if run["properties"]["template"]["containers"][0].get("args") != command(bundle()):
            raise SystemExit("That execution did not run this payload")
        query = ("ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '" + args.execution
                 + "' | where Log_s startswith '" + PREFIX.strip() + "' | order by TimeGenerated asc | project Log_s")
        try:
            tables = stage1.request("POST", f"{stage1.LOGS}/v1/workspaces/{stage1.WORKSPACE}/query",
                                    data={"query": query}, audience=stage1.LOGS)["tables"]
        except SystemExit as error:
            raise SystemExit(f"{error}. The execution's status is {run['properties']['status']}; "
                             "Succeeded means it passed, Failed means it did not.") from None
        records = [json.loads(row[0].removeprefix(PREFIX)) for row in tables[0]["rows"]]
        print(json.dumps({"execution": args.execution, "status": run["properties"]["status"],
                          "records": records}, indent=2, sort_keys=True))
        if not records:
            print("No result line yet; Log Analytics can take a few minutes. Run result again.")
            return 2
        return 0 if records[-1].get("status") == "pass" else 1

    state = preflight()
    summary = {
        "subscription": stage1.SUB, "job": JOB, "job_image": state["container"]["image"], "commit": state["head"],
        "migrations_on_main": len(state["bundle"]["expected"]),
        "migrations": {name: state["bundle"]["expected"][name] for name in TARGETS},
        "payload_sha256": payload_sha256(state["bundle"]),
    }
    if args.stage == "check":
        print(json.dumps(summary, indent=2))
        print("Read-only check passed. Nothing was changed.")
        return 0
    run = stage1.request("POST", JOB_PATH + "/start" + API, data=template(state["container"], command(state["bundle"])))
    print(json.dumps(dict(summary, execution=run.get("name")), indent=2))
    print("Started. Run: python scripts/rollout_onboarding_tables.py result --execution " + str(run.get("name")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
