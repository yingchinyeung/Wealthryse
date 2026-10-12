"""Trusted, catalog-only worker for ONE registered private PostgreSQL host.

Inert on import: no environment/secret discovery, network, SQL, CLI or model.
The separate trusted controller supplies the one secret directly and injects
psycopg.connect; it must reject inherited PG* configuration before launching.
This is an administrator observation, NOT a restricted LOGIN, permission grant,
provider semantic approval, owner authentication or process-isolation proof.
Startup read-only settings constrain this reviewed SELECT-only worker; they do
not turn the administrator credential into a restricted database capability.

The complete catalog is validated and the connection is closed before any
protocol frame is emitted. The deliberate bounded hold permits actual running
replica observation; it is not a delivery guarantee or authority to redispatch.
Output failures can still interrupt stdout: the controller must reject missing
frames and inspect the consumed execution rather than start another job.
"""
from pathlib import Path
import re
import time
from urllib.parse import parse_qsl, quote, unquote, urlsplit

from psycopg.rows import dict_row

import inspect_owner_task_isolated_provider as collector
from ibkr_etl import owner_codex_catalog_transport as codec
from ibkr_etl import owner_codex_isolated_target as registry


OPERATION_ID = "4482b962-755d-4eda-987d-d954fc288143"
WORKER_SOURCE_PATHS = ("src/ibkr_etl/owner_codex_isolated_target.py",
    "src/ibkr_etl/owner_codex_catalog_transport.py",
    "scripts/inspect_owner_task_isolated_provider.py",
    "scripts/run_owner_task_isolated_provider_observation.py")
CA_PATH = "/etc/ssl/certs/ca-certificates.crt"
HOLD_SECONDS = 300
CONNECT_SECONDS = 20
OPTIONS = ("-c default_transaction_read_only=on -c search_path=pg_catalog "
           "-c statement_timeout=30000 -c lock_timeout=3000 "
           "-c idle_in_transaction_session_timeout=30000")
APPLICATION_NAME = "ownertrial-provider-observation"
SETTINGS_SQL = ("SELECT pg_catalog.current_setting('default_transaction_read_only') AS default_readonly,"
    "pg_catalog.current_setting('transaction_read_only') AS readonly,"
    "pg_catalog.current_setting('search_path') AS search_path,"
    "pg_catalog.current_setting('statement_timeout') AS statement_timeout,"
    "pg_catalog.current_setting('lock_timeout') AS lock_timeout,"
    "pg_catalog.current_setting('idle_in_transaction_session_timeout') AS idle_timeout")
SETTINGS = dict(default_readonly="on", readonly="on", search_path="pg_catalog",
                statement_timeout="30s", lock_timeout="3s", idle_timeout="30s")
_CODES = frozenset({"provider_worker_binding_invalid", "provider_worker_secret_invalid",
    "provider_worker_ca_missing", "provider_worker_transport_mismatch",
    "provider_worker_session_unsafe", "provider_worker_observation_failed",
    "provider_worker_close_failed", "provider_worker_emit_failed"})


class ProviderWorkerError(ValueError):
    """Fixed code only; never retain a connector exception, DSN or password."""
    def __init__(self, code):
        self.code = code if type(code) is str and code in _CODES else "provider_worker_observation_failed"
        super().__init__(self.code)


def require(condition, code):
    if not condition:
        raise ProviderWorkerError(code)


def fixed_binding(values):
    """Copy native fields and require the exact reviewed operation, not any UUID."""
    try:
        require(type(values) is dict and set(values) == set(codec.BINDING_FIELDS)
            and all(type(values[k]) is str for k in codec.BINDING_FIELDS), "provider_worker_binding_invalid")
        result = codec.Binding(**values)
        require(result.purpose == "provider" and result.profile_key == registry.PROFILE_KEY
            and result.job_resource_id == registry.JOB_ID and result.operation_id == OPERATION_ID,
            "provider_worker_binding_invalid")
        return result
    except ProviderWorkerError:
        raise
    except Exception:
        raise ProviderWorkerError("provider_worker_binding_invalid") from None


def validate_admin_secret(value):
    """Private parser; returned password-containing kwargs stay in trusted code.

    Require the canonical URL produced by the reviewed host creator. libpq DSN,
    percent-encoding aliases, duplicate fields, options/services, alternate CA,
    omitted port, alternate database and weak TLS modes are not accepted.
    """
    try:
        require(type(value) is str and 1 <= len(value) <= 4096 and value.isascii()
            and all(char.isprintable() for char in value), "provider_worker_secret_invalid")
        require(re.search(r"%(?![0-9A-Fa-f]{2})", value) is None, "provider_worker_secret_invalid")
        parsed = urlsplit(value)
        require(parsed.scheme == "postgresql" and parsed.hostname == registry.HOST
            and parsed.port == 5432 and parsed.path == "/postgres" and not parsed.fragment
            and parsed.username is not None and parsed.password is not None,
            "provider_worker_secret_invalid")
        username = unquote(parsed.username, encoding="utf-8", errors="strict")
        password = unquote(parsed.password, encoding="utf-8", errors="strict")
        require(username == registry.ADMIN and type(password) is str
            and re.fullmatch(r"Aa9![A-Za-z0-9_-]{64}", password) is not None,
            "provider_worker_secret_invalid")
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True,
                          encoding="utf-8", errors="strict")
        require(len(pairs) == 2 and len({key for key, _ in pairs}) == 2
            and dict(pairs) == dict(sslmode="verify-full", sslrootcert=CA_PATH), "provider_worker_secret_invalid")
        expected = "postgresql://" + registry.ADMIN + ":" + quote(password, safe="") + "@" + registry.HOST
        expected += ":5432/postgres?sslmode=verify-full&sslrootcert=" + quote(CA_PATH, safe="")
        require(value == expected, "provider_worker_secret_invalid")
        return dict(host=registry.HOST, user=registry.ADMIN, password=password,
            dbname=registry.MANAGEMENT_DATABASE, port="5432", sslmode="verify-full", sslrootcert=CA_PATH)
    except ProviderWorkerError:
        raise
    except Exception:
        raise ProviderWorkerError("provider_worker_secret_invalid") from None


def require_ca_bundle():
    """Stat only the fixed runtime CA bundle; no credential or arbitrary path."""
    try:
        path = Path(CA_PATH)
        require(path.is_file() and not path.is_symlink(), "provider_worker_ca_missing")
    except ProviderWorkerError:
        raise
    except Exception:
        raise ProviderWorkerError("provider_worker_ca_missing") from None


def transport_context(raw):
    """Inspect only named non-secret libpq parameters; never access password.val."""
    wanted = dict(host=registry.HOST, user=registry.ADMIN, dbname=registry.MANAGEMENT_DATABASE,
        port="5432", sslmode="verify-full", sslrootcert=CA_PATH, connect_timeout=str(CONNECT_SECONDS),
        options=OPTIONS, application_name=APPLICATION_NAME)
    try:
        selected = {}
        # get_parameters() filters compiled defaults (e.g. port); raw options
        # retain the actual setting. Do not decode/retain any other option value.
        for option in raw.pgconn.info:
            key = option.keyword
            require(type(key) is bytes, "provider_worker_transport_mismatch")
            if key in {name.encode("ascii") for name in wanted}:
                name = key.decode("ascii")
                require(name not in selected and type(option.val) is bytes, "provider_worker_transport_mismatch")
                selected[name] = option.val.decode("ascii")
        require(selected == wanted and raw.pgconn.ssl_in_use is True,
            "provider_worker_transport_mismatch")
    except ProviderWorkerError:
        raise
    except Exception:
        raise ProviderWorkerError("provider_worker_transport_mismatch") from None


def session_context(raw):
    # The existing collector's safe, uncast path query must precede casts or any
    # catalog query. No SET, SET ROLE, transaction block or caller SQL is used.
    require(raw.autocommit is True and isinstance(raw.info.transaction_status, int)
        and type(raw.info.transaction_status) is not bool and raw.info.transaction_status == 0,
        "provider_worker_session_unsafe")
    require(all(type(getattr(raw.info, key)) is str for key in ("host", "dbname", "user"))
        and raw.info.host == registry.HOST and raw.info.dbname == registry.MANAGEMENT_DATABASE
        and raw.info.user == registry.ADMIN, "provider_worker_transport_mismatch")
    transport_context(raw)
    collector._path(raw)
    value = raw.execute(SETTINGS_SQL).fetchone()
    require(type(value) is dict and set(value) == set(SETTINGS)
        and all(type(value[key]) is str and value[key] == expected for key, expected in SETTINGS.items()),
        "provider_worker_session_unsafe")


def observe(binding_values, admin_secret, *, connect):
    """Trusted injectable connector; return complete frames only after close.

    Inputs are not a model tool surface. The reviewed launch environment, image,
    actual source SHA, single consumed dispatch and log retention are separately
    checked by the controller. No retry is performed on any failure.
    """
    raw = None
    pending = None
    record = None
    try:
        binding = fixed_binding(binding_values)
        parameters = validate_admin_secret(admin_secret)
        require_ca_bundle()
        require(callable(connect), "provider_worker_observation_failed")
        raw = connect(**parameters, autocommit=True, connect_timeout=CONNECT_SECONDS,
            row_factory=dict_row, options=OPTIONS, application_name=APPLICATION_NAME)
        # Dropping these references is NOT a claim of secure memory erasure or
        # process isolation. The caller and libpq still own trusted credentials.
        password = parameters.pop("password")
        parameters.clear()
        session_context(raw)
        record = collector.inspect_provider(raw)
        session_context(raw)
        collector.validate_record(record)
        payload = codec.canonical(record)
        require(password.encode("ascii") not in payload and admin_secret.encode("ascii") not in payload,
            "provider_worker_observation_failed")
        del password
        del admin_secret
        lines = codec.encode_record(record, binding)  # Full validation before any stdout.
    except ProviderWorkerError as error:
        pending = error.code
    except Exception:
        pending = "provider_worker_observation_failed"
    finally:
        if raw is not None:
            try:
                raw.close()
            except Exception:
                pending = "provider_worker_close_failed"
    if pending is not None:
        raise ProviderWorkerError(pending) from None
    return lines


def main(binding_values, admin_secret, *, connect, emit=print, hold=time.sleep):
    """No CLI or ambient credential discovery; controller owns sanitized exit."""
    lines = observe(binding_values, admin_secret, connect=connect)
    # Reduce one redundant trusted reference during output/hold. This is not
    # secure erasure: the caller/libpq/runtime may still own credential memory.
    del admin_secret
    try:
        for line in lines:
            emit(line, flush=True)
        hold(HOLD_SECONDS)
    except Exception:
        raise ProviderWorkerError("provider_worker_emit_failed") from None
    manifest = codec.parse_line(lines[-1], fixed_binding(binding_values))
    return dict(status="complete_provider_catalog_emitted", line_count=len(lines),
        record_bytes=manifest["record_bytes"], record_sha256=manifest["record_sha256"],
        provider_semantics_verified=False, restricted_login_verified=False,
        credential_isolation_verified=False, protected_retention_verified=False)
