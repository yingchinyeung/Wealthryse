"""Store the IBKR OAuth credentials in Key Vault as ibkr-oauth-* secrets.

Run on the owner's computer, from a clone of main, after `az login`, with the same Python
as the other rollout scripts (requests and azure-identity):

    python scripts/store_ibkr_oauth.py "D:\\IBKR Data\\ibkr-oauth"

The folder is the one ibkr_oauth_keygen.py filled. The two private keys and the DH prime
are read from it; the consumer key, access token and access token secret are asked for at
hidden prompts (twice each). Each value is written with a direct PUT on the
Microsoft.KeyVault/vaults/secrets resource through Azure Resource Manager, which the
vault's network rules do not block. No value is echoed, printed or put on a command line.
A secret that already exists is left alone unless --replace is given.

Secrets: ibkr-oauth-consumer-key, ibkr-oauth-access-token, ibkr-oauth-access-token-secret,
ibkr-oauth-signature-key, ibkr-oauth-encryption-key, ibkr-oauth-dh-prime.
"""
from __future__ import annotations

import argparse
import getpass
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

FILE_SECRETS = (
    ("ibkr-oauth-signature-key", "private_signature.pem", "application/x-pem-file"),
    ("ibkr-oauth-encryption-key", "private_encryption.pem", "application/x-pem-file"),
    ("ibkr-oauth-dh-prime", "dh_prime.txt", "text/plain"),
)
PROMPT_SECRETS = (
    ("ibkr-oauth-consumer-key", "Consumer key (9 letters)"),
    ("ibkr-oauth-access-token", "Access token"),
    ("ibkr-oauth-access-token-secret", "Access token secret"),
)
_CONSUMER_KEY = re.compile(r"^[A-Z]{9}$")


def ask(label: str, prompt=getpass.getpass) -> str:
    value = prompt(label + " (hidden): ").strip()
    if not value:
        raise SystemExit(label + " is empty; nothing more was written.")
    if prompt("Again, to confirm: ").strip() != value:
        raise SystemExit("The two entries for " + label + " differ; nothing more was written.")
    return value


def read_file(folder: Path, name: str) -> str:
    path = folder / name
    if not path.is_file():
        raise SystemExit(f"{path} is missing; run scripts/ibkr_oauth_keygen.py first.")
    text = path.read_text(encoding="ascii").strip()
    if name.endswith(".pem") and "PRIVATE KEY" not in text:
        raise SystemExit(f"{name} is not a private key.")
    if name == "dh_prime.txt" and not re.fullmatch(r"[0-9a-fA-F]{256,}", text):
        raise SystemExit("dh_prime.txt is not a hex prime of at least 1024 bits.")
    return text


def main(argv: list[str] | None = None, prompt=getpass.getpass, stage1=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--replace", action="store_true", help="overwrite secrets that already exist")
    args = parser.parse_args(argv)
    if stage1 is None:
        import rollout_live_data_stage1 as stage1  # noqa: PLC0415 - Azure helpers, owner's computer only

    def path(name: str) -> str:
        return stage1.BASE + "Microsoft.KeyVault/vaults/" + stage1.VAULT + "/secrets/" + name + "?api-version=2023-07-01"

    values: list[tuple[str, str, str]] = [
        (name, read_file(args.folder, file), kind) for name, file, kind in FILE_SECRETS]
    report = {}
    for name, label in PROMPT_SECRETS:
        if stage1.request("GET", path(name), missing=True) is not None and not args.replace:
            report[name] = "exists, left as it is (use --replace to change it)"
            continue
        value = ask(label, prompt)
        if name == "ibkr-oauth-consumer-key":
            value = value.upper()
            if not _CONSUMER_KEY.match(value):
                raise SystemExit("The consumer key should be the 9 letters chosen on IBKR's OAuth page.")
        values.append((name, value, "text/plain"))
    for name, value, kind in values:
        exists = stage1.request("GET", path(name), missing=True) is not None
        if exists and not args.replace:
            report.setdefault(name, "exists, left as it is (use --replace to change it)")
            continue
        stage1.request("PUT", path(name), data={
            "properties": {"value": value, "contentType": kind},
            "tags": {"purpose": "ibkr-oauth-web-api"}})
        report[name] = "replaced" if exists else "created"
    values.clear()
    print(json.dumps({"key_vault": stage1.VAULT, "secrets": report}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
