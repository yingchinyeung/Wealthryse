"""Switch the research zone on (docs/RESEARCH_ZONE.md, Mon 9 Nov). Each step is one command line.

Every step prints what it would do and changes nothing unless given --apply. Each can be
run again: what already exists is kept. Secrets are generated here, stored in Key Vault and
never printed or written to the repository.

  secrets [--apply]   (Ying's PC, az login) create the research secrets in Key Vault if
                      missing: research-db-admin-password, research-db-admin-url,
                      research-db-url and research-live-reader-url.
  deploy [--apply] [--image-digest sha256:... --code-version <commit>] [--vendor-secret NAME]
                      (Ying's PC) deploy infra/research.bicep. Without --image-digest: the
                      server, containers, identity and roles. With it: also the two jobs.
                      The administrator password is read from Key Vault into a temporary
                      parameters file that is deleted straight after.
  roles [--apply]     (the admin VM, inside the network, az login) create the research_reader
                      login on the live server and the research schema and job login on the
                      research server, then print every privilege each has. Never between
                      07:50 and 09:25 UTC. Run it again after U8 (silver history) goes live, so
                      the reader is granted the <table>_as_known functions too.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets as secret_values
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, time, timezone
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
RESOURCE_GROUP = "rg-ibkr-database-v11"
VAULT = "ibkrv2stg-auhecwc4itaoo"
SERVER = "ibkrv2stg-research-pg"
RESEARCH_HOST = f"{SERVER}.postgres.database.azure.com"
RESEARCH_DATABASE = "research"
RESEARCH_ADMIN = "researchadmin"
LIVE_HOST = "ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com"
LIVE_DATABASE = "ibkr_staging"
LIVE_ADMIN_SECRET = "database-admin-url"  # the bootstrap job's; read on the admin VM only
TEMPLATE = ROOT / "infra" / "research.bicep"
DAILY_WINDOW = (time(7, 50), time(9, 25))
SECRETS = {
    "research-db-admin-password": "the research server's administrator password",
    "research-db-admin-url": f"postgresql://{RESEARCH_ADMIN}:<password>@{RESEARCH_HOST}:5432/{RESEARCH_DATABASE}?sslmode=require",
    "research-db-url": f"postgresql://research_job:<password>@{RESEARCH_HOST}:5432/{RESEARCH_DATABASE}?sslmode=require",
    "research-live-reader-url": f"postgresql://research_reader:<password>@{LIVE_HOST}:5432/{LIVE_DATABASE}?sslmode=require",
}


class StepError(RuntimeError):
    """A step stopped; what it already created is kept and the step can be run again."""


def _password() -> str:
    return secret_values.token_urlsafe(32)  # URL-safe, so it needs no quoting in the URLs


def _url(login: str, password: str, host: str, database: str) -> str:
    return f"postgresql://{quote(login)}:{quote(password, safe='')}@{host}:5432/{database}?sslmode=require"


def az(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    run = subprocess.run(["az", *args], capture_output=True, text=True)
    if check and run.returncode:
        raise StepError(f"az {' '.join(args[:3])} failed: {run.stderr.strip()[-500:]}")
    return run


def read_secret(name: str) -> str | None:
    run = az("keyvault", "secret", "show", "--vault-name", VAULT, "--name", name, "--query", "value", "-o", "tsv",
             check=False)
    if run.returncode == 0:
        return run.stdout.rstrip("\n")
    if "SecretNotFound" in run.stderr or "was not found" in run.stderr:
        return None
    raise StepError(f"Key Vault secret {name} could not be read: {run.stderr.strip()[-300:]}")


def _private_file(folder: Path, name: str, text: str) -> Path:
    path = folder / name
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def store_secret(name: str, value: str) -> None:
    folder = Path(tempfile.mkdtemp(prefix="research-secret-"))
    try:
        path = _private_file(folder, "value", value)
        az("keyvault", "secret", "set", "--vault-name", VAULT, "--name", name, "--file", str(path),
           "--encoding", "utf-8", "--content-type", "text/plain", "--tags", "layer=research", "hop=research",
           "--query", "id", "-o", "tsv")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def step_secrets(apply: bool) -> dict:
    if not apply:
        return {"step": "secrets", "dry_run": True, "vault": VAULT, "creates_if_missing": SECRETS}
    created = []
    admin_password = read_secret("research-db-admin-password")
    if admin_password is None:
        admin_password = _password()
        store_secret("research-db-admin-password", admin_password)
        created.append("research-db-admin-password")
    wanted = {
        "research-db-admin-url": lambda: _url(RESEARCH_ADMIN, admin_password, RESEARCH_HOST, RESEARCH_DATABASE),
        "research-db-url": lambda: _url("research_job", _password(), RESEARCH_HOST, RESEARCH_DATABASE),
        "research-live-reader-url": lambda: _url("research_reader", _password(), LIVE_HOST, LIVE_DATABASE),
    }
    for name, make in wanted.items():
        if read_secret(name) is None:
            store_secret(name, make())
            created.append(name)
    return {"step": "secrets", "vault": VAULT, "created": created,
            "kept": sorted(set(SECRETS) - set(created))}


def server_state() -> str | None:
    run = az("postgres", "flexible-server", "show", "--resource-group", RESOURCE_GROUP, "--name", SERVER,
             "--query", "state", "-o", "tsv", check=False)
    return run.stdout.strip() if run.returncode == 0 else None


def ensure_server_ready() -> str | None:
    """A stopped server cannot be updated or reached: start it (the next job run stops it)."""
    state = server_state()
    if state == "Stopped":
        az("postgres", "flexible-server", "start", "--resource-group", RESOURCE_GROUP, "--name", SERVER)
        state = server_state()
    return state


def deployment_parameters(*, image_digest: str, code_version: str, vendor_secrets: list[str]) -> dict:
    if image_digest and not (image_digest.startswith("sha256:") and len(image_digest) == 71):
        raise StepError("--image-digest is sha256: and 64 hex digits, from release_research_image.py build")
    if image_digest and not code_version:
        raise StepError("--code-version (the commit the image was built from) goes with --image-digest")
    return {"imageDigest": {"value": image_digest}, "codeVersion": {"value": code_version or "unknown"},
            "vendorSecretNames": {"value": list(vendor_secrets)}}


def step_deploy(apply: bool, *, image_digest: str, code_version: str, vendor_secrets: list[str]) -> dict:
    parameters = deployment_parameters(image_digest=image_digest, code_version=code_version,
                                       vendor_secrets=vendor_secrets)
    stage = "jobs" if image_digest else "foundation"
    name = f"research-{stage}-{datetime.now(timezone.utc):%Y%m%d%H%M}"
    command = ["deployment", "group", "create", "--resource-group", RESOURCE_GROUP, "--name", name,
               "--template-file", str(TEMPLATE), "--parameters", "@<temporary parameters file>",
               "--query", "properties.outputs", "-o", "json"]
    if not apply:
        return {"step": "deploy", "dry_run": True, "stage": stage, "command": "az " + " ".join(command),
                "parameters": {**{key: value["value"] for key, value in parameters.items()},
                               "administratorLoginPassword": "<from Key Vault research-db-admin-password>"}}
    password = read_secret("research-db-admin-password")
    if not password:
        raise StepError("Run the secrets step first: research-db-admin-password is not in Key Vault")
    for needed in ("research-db-url", "research-live-reader-url", *vendor_secrets):
        if read_secret(needed) is None:
            raise StepError(f"Key Vault secret {needed} is missing; the identity's role on it needs it to exist")
    state = ensure_server_ready()
    folder = Path(tempfile.mkdtemp(prefix="research-deploy-"))
    try:
        document = {"$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
                    "contentVersion": "1.0.0.0",
                    "parameters": {**parameters, "administratorLoginPassword": {"value": password}}}
        path = _private_file(folder, "parameters.json", json.dumps(document))
        command[command.index("@<temporary parameters file>")] = f"@{path}"
        outputs = json.loads(az(*command).stdout or "{}")
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return {"step": "deploy", "stage": stage, "deployment": name, "server_state_before": state,
            "outputs": {key: value.get("value") for key, value in outputs.items()}}


def in_daily_window(moment: datetime) -> bool:
    now = moment.astimezone(timezone.utc).time()
    return DAILY_WINDOW[0] <= now <= DAILY_WINDOW[1]


def step_roles(apply: bool, *, now: datetime | None = None) -> dict:
    scripts = [ROOT / "scripts" / "research" / "live_reader_role.py",
               ROOT / "scripts" / "research" / "research_server_roles.py"]
    if not apply:
        sql = {path.name: subprocess.run([sys.executable, str(path), "--dry-run"], capture_output=True, text=True,
                                         check=True).stdout for path in scripts}
        return {"step": "roles", "dry_run": True, "runs": [f"{path.name} --apply" for path in scripts],
                "environment": ["DATABASE_ADMIN_URL (Key Vault database-admin-url)",
                                "RESEARCH_LIVE_READER_URL (research-live-reader-url)",
                                "RESEARCH_DATABASE_ADMIN_URL (research-db-admin-url)",
                                "RESEARCH_DATABASE_URL (research-db-url)"],
                "sql": sql}
    if in_daily_window(now or datetime.now(timezone.utc)):
        raise StepError("Not between 07:50 and 09:25 UTC: the live server is busy with the daily chain")
    environment = dict(os.environ)
    for variable, name in (("DATABASE_ADMIN_URL", LIVE_ADMIN_SECRET), ("RESEARCH_LIVE_READER_URL", "research-live-reader-url"),
                           ("RESEARCH_DATABASE_ADMIN_URL", "research-db-admin-url"),
                           ("RESEARCH_DATABASE_URL", "research-db-url")):
        value = read_secret(name)
        if not value:
            raise StepError(f"Key Vault secret {name} is missing")
        environment[variable] = value
    state = ensure_server_ready()
    results = {}
    for path in scripts:
        run = subprocess.run([sys.executable, str(path), "--apply"], capture_output=True, text=True, env=environment,
                             cwd=ROOT)
        lines = [line for line in run.stdout.splitlines() if line.startswith(("RESEARCH_READER ", "RESEARCH_SERVER_ROLES "))]
        results[path.name] = json.loads(lines[-1].split(" ", 1)[1]) if lines else {"status": "failed",
                                                                                  "stderr": run.stderr[-500:]}
        if run.returncode:
            raise StepError(f"{path.name} --apply failed: {json.dumps(results[path.name])[:800]}")
    return {"step": "roles", "research_server_state": state, "results": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="step", required=True)
    for name in ("secrets", "roles"):
        sub.add_parser(name).add_argument("--apply", action="store_true")
    deploy = sub.add_parser("deploy")
    deploy.add_argument("--apply", action="store_true")
    deploy.add_argument("--image-digest", default="")
    deploy.add_argument("--code-version", default="")
    deploy.add_argument("--vendor-secret", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        if args.step == "secrets":
            result = step_secrets(args.apply)
        elif args.step == "deploy":
            result = step_deploy(args.apply, image_digest=args.image_digest, code_version=args.code_version,
                                 vendor_secrets=args.vendor_secret)
        else:
            result = step_roles(args.apply)
    except StepError as error:
        print("RESEARCH_SWITCH_ON " + json.dumps({"step": args.step, "status": "stopped", "reason": str(error)}))
        return 1
    print("RESEARCH_SWITCH_ON " + json.dumps({"status": "done", **result}, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
