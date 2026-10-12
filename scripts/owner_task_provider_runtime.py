"""Secretless metadata qualification of one candidate Linux image.

Import is inert. The trusted launcher must compare the returned descriptor with
an independently retained descriptor before obtaining its one credential.
This checks installed runtime compatibility, not OS/dependency security, live
certificate/hostname verification, SQL permission closure or model isolation.
No connector, cloud API, subprocess, model, arbitrary file or secret value is
used. Ambient environment KEY NAMES are inspected; values are never copied.
"""
import hashlib
from importlib import metadata
import os
from pathlib import Path
import platform
import re
import ssl
import stat
import struct
import sys


CA_PATH = "/etc/ssl/certs/ca-certificates.crt"
MAX_CA_BYTES = 2 * 1024 * 1024
TLS_OVERRIDE_KEYS = frozenset(("SSL_CERT_FILE", "SSL_CERT_DIR", "OPENSSL_CONF", "OPENSSL_MODULES"))
FALSE_FLAGS = ("tls_connection_verified", "provider_semantics_verified",
    "restricted_login_verified", "model_credential_containment_verified",
    "os_or_dependency_security_certification", "owner_runtime_active", "six_specialist_runtimes_active")
ZERO_COUNTS = ("database_connections", "sql_calls", "model_calls", "credential_values_read")
FIELDS = frozenset(("schema_version", "observation", "python_implementation", "python_version",
    "system", "machine", "pointer_bits", "effective_uid", "effective_gid", "psycopg_version",
    "psycopg_binary_version", "pq_implementation", "libpq_version", "libpq_build_version",
    "ca_path", "ca_bytes", "ca_sha256", "ca_certificates", "pg_environment_absent") + FALSE_FLAGS + ZERO_COUNTS)


class RuntimeCheckError(ValueError):
    """No exception text from libraries, filesystem or environment is exposed."""


def require(condition, code="provider_runtime_invalid"):
    if not condition:
        raise RuntimeCheckError(code)


def reject_connection_environment(environ=None):
    """Reject overrides, do not silently strip them or inspect their values."""
    try:
        if environ is None:
            keys = list(os.environ.keys())
        else:
            require(type(environ) is dict, "provider_runtime_environment_invalid")
            keys = list(environ.keys())
        require(all(type(key) is str and 0 < len(key) <= 512 and key.isascii()
            and all(char.isprintable() for char in key) for key in keys), "provider_runtime_environment_invalid")
        require(not any(key.upper().startswith("PG") or key.upper() in TLS_OVERRIDE_KEYS for key in keys),
            "provider_runtime_connection_override")
    except RuntimeCheckError:
        raise
    except Exception:
        raise RuntimeCheckError("provider_runtime_environment_invalid") from None


def validate_descriptor(value):
    """Pure, bounded native-schema check and copy; no live acceptance authority."""
    require(type(value) is dict and set(value) == FIELDS)
    for name in FALSE_FLAGS:
        require(value[name] is False)
    for name in ZERO_COUNTS:
        require(type(value[name]) is int and value[name] == 0)
    require(type(value["schema_version"]) is int and value["schema_version"] == 1
        and type(value["observation"]) is str and value["observation"] == "image_runtime_metadata_only")
    require(type(value["python_implementation"]) is str and value["python_implementation"] == "CPython"
        and type(value["python_version"]) is str and re.fullmatch(r"3\.12\.[0-9]{1,3}", value["python_version"]) is not None)
    require(type(value["system"]) is str and value["system"] == "Linux"
        and type(value["machine"]) is str and value["machine"] == "x86_64")
    for name, expected in (("pointer_bits", 64), ("effective_uid", 10001), ("effective_gid", 10001)):
        require(type(value[name]) is int and value[name] == expected)
    for name in ("psycopg_version", "psycopg_binary_version"):
        require(type(value[name]) is str and re.fullmatch(r"3\.[0-9]{1,3}\.[0-9]{1,3}", value[name]) is not None)
        require((3, 2, 0) <= tuple(int(part) for part in value[name].split(".")) < (4, 0, 0))
    require(value["psycopg_version"] == value["psycopg_binary_version"]
        and type(value["pq_implementation"]) is str and value["pq_implementation"] == "binary")
    for name in ("libpq_version", "libpq_build_version"):
        require(type(value[name]) is int and 160000 <= value[name] < 200000)
    require(value["libpq_version"] == value["libpq_build_version"])
    require(type(value["ca_path"]) is str and value["ca_path"] == CA_PATH
        and type(value["ca_bytes"]) is int and 0 < value["ca_bytes"] <= MAX_CA_BYTES
        and type(value["ca_sha256"]) is str and re.fullmatch("[0-9a-f]{64}", value["ca_sha256"]) is not None
        and type(value["ca_certificates"]) is int and 1 <= value["ca_certificates"] <= 4096
        and value["pg_environment_absent"] is True)
    return dict(value)


def ca_descriptor():
    """Read only the fixed CA file with no-follow and replacement/drift checks."""
    fd = None
    try:
        path = Path(CA_PATH)
        before = path.lstat()
        require(stat.S_ISREG(before.st_mode) and not stat.S_ISLNK(before.st_mode), "provider_runtime_ca_invalid")
        require(type(before.st_size) is int and 0 < before.st_size <= MAX_CA_BYTES, "provider_runtime_ca_invalid")
        require(hasattr(os, "O_NOFOLLOW"), "provider_runtime_ca_invalid")
        fd = os.open(CA_PATH, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        opened = os.fstat(fd)
        signature = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        require(stat.S_ISREG(opened.st_mode) and signature(opened) == signature(before), "provider_runtime_ca_invalid")
        pieces = []
        remaining = MAX_CA_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            pieces.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(pieces)
        require(0 < len(raw) <= MAX_CA_BYTES and len(raw) == before.st_size
            and signature(os.fstat(fd)) == signature(before) and signature(path.lstat()) == signature(before),
            "provider_runtime_ca_invalid")
        # Loading the captured bytes avoids a second path lookup during parsing.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        require(context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname is True, "provider_runtime_ca_invalid")
        context.load_verify_locations(cadata=raw.decode("ascii", errors="strict"))
        certificates = context.cert_store_stats()["x509_ca"]
        require(type(certificates) is int and 1 <= certificates <= 4096, "provider_runtime_ca_invalid")
        return dict(ca_path=CA_PATH, ca_bytes=len(raw), ca_sha256=hashlib.sha256(raw).hexdigest(), ca_certificates=certificates)
    except RuntimeCheckError:
        raise
    except Exception:
        raise RuntimeCheckError("provider_runtime_ca_invalid") from None
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                raise RuntimeCheckError("provider_runtime_ca_invalid") from None


def check_runtime(environ=None):
    """No network, SQL or secret values; caller must independently bind image."""
    reject_connection_environment(environ)
    try:
        require(sys.implementation.name == "cpython" and sys.version_info[:2] == (3, 12)
            and sys.platform == "linux", "provider_runtime_platform_invalid")
        import psycopg
        from psycopg import pq
        from psycopg.rows import dict_row
        require(callable(dict_row), "provider_runtime_library_invalid")
        descriptor = dict(schema_version=1, observation="image_runtime_metadata_only",
            python_implementation=platform.python_implementation(), python_version=platform.python_version(),
            system=platform.system(), machine=platform.machine(), pointer_bits=struct.calcsize("P") * 8,
            effective_uid=os.geteuid(), effective_gid=os.getegid(), psycopg_version=psycopg.__version__,
            psycopg_binary_version=metadata.version("psycopg-binary"), pq_implementation=pq.__impl__,
            libpq_version=pq.version(), libpq_build_version=pq.__build_version__, pg_environment_absent=True,
            **ca_descriptor(), **{key: False for key in FALSE_FLAGS}, **{key: 0 for key in ZERO_COUNTS})
        return validate_descriptor(descriptor)
    except RuntimeCheckError:
        raise
    except Exception:
        raise RuntimeCheckError("provider_runtime_library_invalid") from None
