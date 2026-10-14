"""Stage 1 of the live IBKR data rollout: migration 063 and the collector login.

Run on the owner's computer, from a clone of main, after `az login`, with a
Python that has requests and azure-identity. Without git, a downloaded copy of
main works too (GitHub's "Download ZIP" for a commit); check prints the same
migration and payload checksums either way, so they can be compared:

    python scripts/rollout_live_data_stage1.py check
    python scripts/rollout_live_data_stage1.py start
    python scripts/rollout_live_data_stage1.py result --execution ibkrv2stg-bootstrap-xxxxxxx

check is read-only. start creates the Key Vault secret database-live-url if it
is missing (a new random password, never printed), then starts one execution of
the existing ibkrv2stg-bootstrap job with an override that runs
live_data_stage1_payload.py. No job, image, identity or secret binding is
changed, and no image is built. result reads the execution's one result line
from Log Analytics; it can take a few minutes to arrive.

The execution gets the admin URL through the job's own database-admin-url
binding, and the collector's password only as a SCRAM-SHA-256 verifier.
"""
from __future__ import annotations

import argparse
import base64
import copy
import gzip
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SUB = "3956e821-cee4-403f-8708-e84e2ed490eb"
RG = "rg-ibkr-database-v11"
BASE = f"/subscriptions/{SUB}/resourceGroups/{RG}/providers/"
JOB = "ibkrv2stg-bootstrap"
JOB_PATH = BASE + "Microsoft.App/jobs/" + JOB
API = "?api-version=2024-03-01"
VAULT = "ibkrv2stg-auhecwc4itaoo"
SECRET = "database-live-url"
IDENTITY_PREFIX = BASE + "Microsoft.ManagedIdentity/userAssignedIdentities/"
HOST = "ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com"
DATABASE = "ibkr_staging"
ROLE = "ibkr_live_collector"
WEB_ROLE = "ibkr_stg_web"
TARGET = "063_live_ibkr_data.sql"
# Migrations production is known not to have applied: 054/055 were skipped when
# 056-058 were installed on their own, and 059-061 are deliberately kept off.
# 063 does not depend on any of them, so they may stay pending; any other gap
# makes the payload refuse.
HELD_BACK = ("054_shadow_workflow_evidence.sql", "055_fx_evidence_versions.sql", "059_owner_codex_tasks.sql",
             "060_shadow_workflow_date_policies.sql", "061_shadow_workflow_leases.sql")
WORKSPACE = "5264a124-10cd-45f6-93a5-6d65c37e9bfb"
PREFIX = "LIVE_DATA_STAGE1 "
PAYLOAD = "scripts/live_data_stage1_payload.py"
ACCESS = "src/ibkr_etl/live_collector_access.py"


def has_git() -> bool:
    """A git clone with git installed; otherwise the files on disk are a downloaded copy."""
    return shutil.which("git") is not None and (ROOT / ".git").exists()


def git_text(path: str, commit: str = "HEAD") -> str:
    """A file as committed, with LF line ends like the Linux images, whatever the checkout does."""
    if has_git():
        data = subprocess.run(["git", "show", f"{commit}:{path}"], cwd=ROOT, capture_output=True, check=True).stdout
    elif commit == "HEAD":
        data = (ROOT / path).read_bytes()
    else:
        raise SystemExit("Reading another commit needs a git clone")
    return data.decode("utf-8").replace("\r\n", "\n")


def bundle(commit: str = "HEAD") -> dict:
    if has_git():
        names = subprocess.run(["git", "ls-tree", "--name-only", commit, "migrations/postgres/"], cwd=ROOT,
                               capture_output=True, text=True, check=True).stdout.split()
    else:
        git_text(PAYLOAD, commit)  # refuses another commit
        names = sorted("migrations/postgres/" + path.name for path in (ROOT / "migrations" / "postgres").iterdir()
                       if path.is_file())
    texts = {Path(name).name: git_text(name, commit) for name in names if name.endswith(".sql")}
    if TARGET not in texts or not set(HELD_BACK) <= set(texts):
        raise SystemExit(f"{TARGET} or a held-back migration is not in {commit}; run this from main")
    return {
        "expected": {name: hashlib.sha256(text.encode("utf-8")).hexdigest() for name, text in sorted(texts.items())},
        "held_back": list(HELD_BACK),
        "target": TARGET,
        "target_sql": texts[TARGET],
        "access_source": git_text(ACCESS, commit),
        "role": ROLE,
        "web_role": WEB_ROLE,
        "database": DATABASE,
    }


def encode(value: dict) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return base64.b64encode(gzip.compress(raw, 9, mtime=0)).decode()


def command(value: dict, commit: str = "HEAD") -> list[str]:
    source = git_text(PAYLOAD, commit)
    marker = "BUNDLE = None  # replaced by rollout_live_data_stage1.py"
    if source.count(marker) != 1:
        raise SystemExit("Payload placeholder missing")
    source = source.replace(marker, "BUNDLE = " + repr(encode(value))) + "\nraise SystemExit(main())\n"
    packed = base64.b64encode(gzip.compress(source.encode(), 9, mtime=0)).decode()
    return ["-c", "import base64,gzip;exec(compile(gzip.decompress(base64.b64decode('" + packed
            + "')),'live-data-stage1','exec'))"]


def scram_verifier(password: str, salt: bytes | None = None, iterations: int = 4096) -> str:
    """PostgreSQL's stored form of a SCRAM-SHA-256 password (RFC 7677). It cannot be used to log in."""
    salt = salt or os.urandom(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b64 = lambda data: base64.b64encode(data).decode()
    return (f"SCRAM-SHA-256${iterations}:{b64(salt)}$"
            f"{b64(hashlib.sha256(client_key).digest())}:{b64(server_key)}")


def collector_url(password: str) -> str:
    return f"postgresql://{ROLE}:{quote(password, safe='')}@{HOST}:5432/{DATABASE}?sslmode=require"


def collector_password(url: str) -> str:
    parts = urlsplit(url)
    password = unquote(parts.password or "")
    if parts.username != ROLE or parts.hostname != HOST or parts.path != "/" + DATABASE or len(password) < 24:
        raise SystemExit(f"Key Vault secret {SECRET} is not a {ROLE} URL for {HOST}/{DATABASE}")
    return password


def recorded_image() -> str:
    return json.loads((ROOT / "deploy" / "deployed-images.json").read_text(encoding="utf-8"))["images"][JOB]


def validate_job(job: dict, image: str) -> dict:
    """The bootstrap job must be the manual, private job we expect; returns its container.

    Its one user-assigned identity is whichever the job has today, as long as
    the admin URL binding uses that same identity.
    """
    properties = job["properties"]
    config = properties["configuration"]
    containers = properties["template"]["containers"]
    identities = list(job.get("identity", {}).get("userAssignedIdentities", {}))
    problems = []
    if len(identities) != 1 or not identities[0].casefold().startswith(IDENTITY_PREFIX.casefold()):
        problems.append("identity")
    if config.get("triggerType") != "Manual" or config.get("replicaRetryLimit") != 0:
        problems.append("trigger or retry")
    if len(containers) != 1 or containers[0].get("name") != "postgres-bootstrap":
        problems.append("container")
    elif containers[0].get("image") != image:
        problems.append("image differs from deploy/deployed-images.json")
    admin = [s for s in config.get("secrets", []) if s.get("name") == "database-admin-url"]
    if (len(admin) != 1 or admin[0].get("keyVaultUrl") != f"https://{VAULT}.vault.azure.net/secrets/database-admin-url"
            or not identities or admin[0].get("identity", "").casefold() != identities[0].casefold()):
        problems.append("admin URL binding")
    if properties["template"].get("initContainers") or properties["template"].get("volumes"):
        problems.append("extra containers or volumes")
    if problems:
        raise SystemExit("The bootstrap job is not as expected: " + ", ".join(problems))
    return containers[0]


def template(container: dict, args: list[str], verifier: str) -> dict:
    value = copy.deepcopy(container)
    value.update(command=["python"], args=args, resources={"cpu": 0.5, "memory": "1Gi"},
                 env=[{"name": "DATABASE_ADMIN_URL", "secretRef": "database-admin-url"},
                      {"name": "LIVE_COLLECTOR_VERIFIER", "value": verifier}])
    for key in ("volumeMounts", "probes"):
        value.pop(key, None)
    return {"containers": [value]}


# Azure calls, imported lazily so the tests need no Azure packages. They use
# the az login's token and never print a response body, which could carry a secret.
MANAGEMENT = "https://management.azure.com"
VAULT_AUDIENCE = "https://vault.azure.net"
LOGS = "https://api.loganalytics.io"


def request(method: str, url: str, *, data=None, audience: str = MANAGEMENT, missing: bool = False):
    import requests
    from azure.identity import AzureCliCredential

    token = AzureCliCredential().get_token(audience + "/.default").token
    if url.startswith("/"):
        url = MANAGEMENT + url
    response = requests.request(method, url, headers={"Authorization": "Bearer " + token}, json=data, timeout=60)
    if missing and response.status_code == 404:
        return None
    if not response.ok:
        # Only Azure's fixed error codes (e.g. ObjectIsDeletedButRecoverable), never the message or body.
        try:
            error = response.json().get("error") or {}
        except ValueError:
            error = {}
        codes = [code for code in (error.get("code"), (error.get("innererror") or {}).get("code"))
                 if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.]{1,80}", code)]
        raise SystemExit(f"Azure {method} {url.split('?')[0]} failed with HTTP {response.status_code}"
                         + (f" ({', '.join(codes)})" if codes else ""))
    return response.json() if response.content else {}


def secret_url() -> str:
    return f"https://{VAULT}.vault.azure.net/secrets/{SECRET}?api-version=7.4"


def preflight() -> dict:
    if request("GET", f"/subscriptions/{SUB}?api-version=2022-12-01").get("state") != "Enabled":
        raise SystemExit("The subscription is not enabled for this login")
    job = request("GET", JOB_PATH + API)
    container = validate_job(job, recorded_image())
    runs = request("GET", JOB_PATH + "/executions" + API).get("value", [])
    busy = [r["name"] for r in runs if r["properties"]["status"] not in ("Succeeded", "Failed", "Stopped")]
    if busy:
        raise SystemExit("The bootstrap job is busy: " + ", ".join(busy))
    value = bundle()
    head = (subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
            if has_git() else "downloaded copy (no git); compare the checksums")
    identity = next(iter(job["identity"]["userAssignedIdentities"])).rsplit("/", 1)[-1]
    return {"container": container, "bundle": value, "head": head, "identity": identity}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", choices=["check", "start", "result"])
    parser.add_argument("--execution")
    args = parser.parse_args(argv)

    if args.stage == "result":
        if not re.fullmatch(JOB + r"-[a-z0-9]{7}", args.execution or ""):
            raise SystemExit("--execution must name an ibkrv2stg-bootstrap execution")
        run = request("GET", JOB_PATH + "/executions/" + args.execution + API)
        if run["properties"]["template"]["containers"][0].get("args") != command(bundle()):
            raise SystemExit("That execution did not run this stage's payload")
        query = ("ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '" + args.execution
                 + "' | where Log_s startswith '" + PREFIX.strip() + "' | order by TimeGenerated asc | project Log_s")
        tables = request("POST", f"{LOGS}/v1/workspaces/{WORKSPACE}/query", data={"query": query},
                         audience=LOGS)["tables"]
        records = [json.loads(row[0].removeprefix(PREFIX)) for row in tables[0]["rows"]]
        print(json.dumps({"execution": args.execution, "status": run["properties"]["status"],
                          "records": records}, indent=2, sort_keys=True))
        if not records:
            if run["properties"]["status"] in ("Succeeded", "Failed", "Stopped"):
                print("The execution has ended but no result line has arrived. Log Analytics can lag a few "
                      "minutes; if it is still missing after that, the payload never reported (see its console logs).")
            else:
                print("No result line yet; Log Analytics can take a few minutes. Run result again.")
            return 2
        return 0 if records[-1].get("status") == "pass" else 1

    state = preflight()
    existing = request("GET", secret_url(), audience=VAULT_AUDIENCE, missing=True)
    summary = {
        "subscription": SUB, "job": JOB, "job_image": state["container"]["image"], "job_identity": state.get("identity"),
        "commit": state["head"], "migrations_on_main": len(state["bundle"]["expected"]),
        "migration": TARGET, "migration_sha256": state["bundle"]["expected"][TARGET],
        "payload_sha256": hashlib.sha256(" ".join(command(state["bundle"])).encode()).hexdigest(),
        "key_vault_secret": SECRET + (" exists" if existing else " will be created"),
    }
    if existing:
        collector_password(existing["value"])
    if args.stage == "check":
        print(json.dumps(summary, indent=2))
        print("Read-only check passed. Nothing was changed.")
        return 0

    if existing is None:
        password = secrets.token_urlsafe(48)
        request("PUT", secret_url(), audience=VAULT_AUDIENCE, data={
            "value": collector_url(password), "contentType": "postgresql-url",
            "tags": {"purpose": "live-ibkr-collector"}})
        existing = request("GET", secret_url(), audience=VAULT_AUDIENCE)
    verifier = scram_verifier(collector_password(existing["value"]))
    del existing
    run = request("POST", JOB_PATH + "/start" + API,
                  data=template(state["container"], command(state["bundle"]), verifier))
    print(json.dumps(dict(summary, execution=run.get("name"), key_vault_secret=SECRET + " ready"), indent=2))
    print("Started. Run: python scripts/rollout_live_data_stage1.py result --execution " + str(run.get("name")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
