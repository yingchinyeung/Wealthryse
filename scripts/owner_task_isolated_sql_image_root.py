"""Inert candidate launcher correction; no dispatch or frozen job mutation.

The pinned platform image sets IBKR_PROJECT_ROOT=/app. Accept only that exact
non-secret build default before module imports, credential reads or SQL. This
candidate needs a separately reviewed new physical attempt; it cannot restart
the consumed named job or establish live/process-isolation acceptance.
"""
import hashlib

import owner_task_isolated_sql_named_launch as prior


OLD_GUARD = '''    if any(k.upper().startswith("PG") or k.upper() in ("SSL_CERT_FILE","SSL_CERT_DIR","OPENSSL_CONF","OPENSSL_MODULES","IBKR_PROJECT_ROOT") for k in os.environ):
        raise ValueError("isolated_sql_ambient_override")'''
NEW_GUARD = '''    if any(type(k) is not str or not 0 < len(k) <= 512 or not k.isascii() or not all(c.isprintable() for c in k) or k.upper().startswith("PG") or k.upper() in ("SSL_CERT_FILE","SSL_CERT_DIR","OPENSSL_CONF","OPENSSL_MODULES") or (k.upper()=="IBKR_PROJECT_ROOT" and k!="IBKR_PROJECT_ROOT") for k in os.environ):
        raise ValueError("isolated_sql_ambient_override")
    if os.environ.get("IBKR_PROJECT_ROOT") != "/app":
        raise ValueError("isolated_sql_image_root_mismatch")'''


def candidate_launcher():
    """Return new source only; preserve the exact frozen predecessor artifact."""
    if prior.LAUNCHER.count(OLD_GUARD) != 1:
        raise ValueError('isolated_sql_predecessor_guard_changed')
    value = prior.LAUNCHER.replace(OLD_GUARD, NEW_GUARD, 1)
    compile(value, 'isolated-sql-image-root-candidate', 'exec')
    return value


def descriptor():
    value = candidate_launcher()
    return dict(status='candidate_not_dispatched',
        predecessor_launcher_sha256=hashlib.sha256(prior.LAUNCHER.encode()).hexdigest(),
        candidate_launcher_sha256=hashlib.sha256(value.encode()).hexdigest(),
        allowed_image_root='/app', other_root_values_allowed=False,
        credential_read_authorized=False, sql_dispatched=False,
        existing_job_changed=False, step_2_complete=False)
