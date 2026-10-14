"""Store the IB Gateway login in Key Vault as ibkr-username and ibkr-password.

Run on the owner's computer, from a clone of main, after `az login`, with the
same Python as stage 1 (requests and azure-identity):

    python scripts/store_ibkr_login.py

It asks for each value at a hidden prompt, twice, and writes it with a direct
PUT on the Microsoft.KeyVault/vaults/secrets resource through Azure Resource
Manager, which the vault's network rules do not block (its data plane is
refused from outside Azure). A value is never echoed, printed, put on a
command line or written to a file; only Azure's error code is shown if a write
fails. A secret that already exists is left alone unless --replace is given.
The gateway VM reads both at service start (deploy/ibkr-gateway/fetch-secrets.sh).
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rollout_live_data_stage1 as stage1  # noqa: E402

SECRETS = (("ibkr-username", "IBKR username"), ("ibkr-password", "IBKR password"))


def secret_path(name: str) -> str:
    return stage1.BASE + "Microsoft.KeyVault/vaults/" + stage1.VAULT + "/secrets/" + name + "?api-version=2023-07-01"


def ask(label: str, prompt=getpass.getpass) -> str:
    value = prompt(label + " (hidden): ")
    if not value or value != value.strip():
        raise SystemExit(label + " is empty or starts or ends with a space; nothing more was written.")
    if prompt("Again, to confirm: ") != value:
        raise SystemExit("The two entries for " + label + " differ; nothing more was written.")
    return value


def main(argv: list[str] | None = None, prompt=getpass.getpass) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--replace", action="store_true", help="overwrite a secret that already exists")
    args = parser.parse_args(argv)

    report = {}
    for name, label in SECRETS:
        exists = stage1.request("GET", secret_path(name), missing=True) is not None
        if exists and not args.replace:
            report[name] = "exists, left as it is (use --replace to change it)"
            continue
        value = ask(label, prompt)
        stage1.request("PUT", secret_path(name), data={
            "properties": {"value": value, "contentType": "text/plain"},
            "tags": {"purpose": "ibkr-gateway-login"}})
        del value
        report[name] = "replaced" if exists else "created"
    print(json.dumps({"key_vault": stage1.VAULT, "secrets": report}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
