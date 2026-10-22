"""Make the five key files IBKR's OAuth page asks for, plus the DH prime as hex.

Run on the owner's computer (no openssl needed; uses the cryptography package that the
platform already installs):

    python scripts/ibkr_oauth_keygen.py "D:\\IBKR Data\\ibkr-oauth"

It writes into that folder, refusing to overwrite anything already there:

- public_signature.pem, public_encryption.pem, dhparam.pem: upload these three on IBKR's
  OAuth page;
- private_signature.pem, private_encryption.pem: keep private; store_ibkr_oauth.py puts
  them in Key Vault;
- dh_prime.txt: the prime from dhparam.pem as one hex line, for Key Vault.

Making the 2048-bit DH parameters can take a minute or two.
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import dh, rsa

FILES = ("private_signature.pem", "public_signature.pem", "private_encryption.pem",
         "public_encryption.pem", "dhparam.pem", "dh_prime.txt")


def _pair() -> tuple[bytes, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.PEM,
                                           serialization.PublicFormat.SubjectPublicKeyInfo)
    return private, public


def make(folder: Path, *, dh_bits: int = 2048) -> dict[str, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    clashes = [name for name in FILES if (folder / name).exists()]
    if clashes:
        raise SystemExit("Already there, nothing written: " + ", ".join(clashes))
    signature_private, signature_public = _pair()
    encryption_private, encryption_public = _pair()
    print("Making DH parameters (a minute or two)...", flush=True)
    with warnings.catch_warnings():  # IBKR's OAuth 1.0a still uses classic Diffie-Hellman
        warnings.simplefilter("ignore")
        parameters = dh.generate_parameters(generator=2, key_size=dh_bits)
    dhparam = parameters.parameter_bytes(serialization.Encoding.PEM, serialization.ParameterFormat.PKCS3)
    prime = format(parameters.parameter_numbers().p, "x")
    contents = {
        "private_signature.pem": signature_private, "public_signature.pem": signature_public,
        "private_encryption.pem": encryption_private, "public_encryption.pem": encryption_public,
        # Unix line endings: IBKR's upload has refused dhparam files with CRLF.
        "dhparam.pem": dhparam.replace(b"\r\n", b"\n"), "dh_prime.txt": (prime + "\n").encode("ascii"),
    }
    written = {}
    for name, data in contents.items():
        path = folder / name
        path.write_bytes(data)
        written[name] = path
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", type=Path)
    args = parser.parse_args(argv)
    make(args.folder)
    print(f"Done. Upload public_signature.pem, public_encryption.pem and dhparam.pem from {args.folder}")
    print("on IBKR's OAuth page. Keep the private_*.pem files and dh_prime.txt private.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
