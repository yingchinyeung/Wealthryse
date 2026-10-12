"""Postgres connections that sign in with the container's managed identity.

The tool server connects with search_path = agent, so the platform's own
calculation code reads the scoped views in migrations/agent instead of the
platform tables. Each tool call runs in its own transaction with exactly one
account in scope.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from ibkr_etl.db import PostgresConnection, _hybrid_row_factory

from .config import DatabaseSettings

POSTGRES_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"


def _password(settings: DatabaseSettings) -> str:
    from azure.identity import ManagedIdentityCredential

    return ManagedIdentityCredential(client_id=settings.managed_identity_client_id).get_token(POSTGRES_SCOPE).token


def connect(settings: DatabaseSettings, *, search_path: str) -> PostgresConnection:
    import psycopg

    if settings.dsn_override:
        raw = psycopg.connect(settings.dsn_override, row_factory=_hybrid_row_factory)
    else:
        if not settings.host or not settings.user:
            raise RuntimeError("AGENT_DB_HOST and AGENT_DB_USER must be set")
        raw = psycopg.connect(
            host=settings.host, dbname=settings.name, user=settings.user,
            password=_password(settings), sslmode="require", connect_timeout=10,
            row_factory=_hybrid_row_factory,
        )
    connection = PostgresConnection(raw)
    connection.execute("SELECT set_config('search_path', ?, false)", (search_path,))
    connection.execute("SELECT set_config('app.tenant_id', ?, false)", (settings.tenant_id,))
    connection.execute("SET statement_timeout = '15s'")
    connection.commit()
    return connection


@contextmanager
def scoped(connection: PostgresConnection, subjects: list[str] | tuple[str, ...]) -> Iterator[PostgresConnection]:
    """A read-only transaction that can see only ``subjects``."""
    if any("," in s or not s for s in subjects):
        raise ValueError("Invalid account identifier")
    try:
        connection.execute("SET TRANSACTION READ ONLY")
        connection.execute("SELECT set_config('agent.subjects', ?, true)", (",".join(subjects),))
        yield connection
    finally:
        # Always roll back: the scope is transaction-local and nothing is written.
        connection.rollback()
