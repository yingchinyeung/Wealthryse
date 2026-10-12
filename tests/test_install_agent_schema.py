import importlib.util
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("install_agent_schema", ROOT / "scripts" / "install_agent_schema.py")
install = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install)


class FakeResult:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, roles=(), accounts=()):
        self.roles = set(roles)
        self.accounts = set(accounts)
        self.statements = []
        self.committed = self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, statement, params=None):
        text = statement if isinstance(statement, str) else statement.as_string(None)
        self.statements.append(text)
        if params and "FROM pg_roles" in text:
            return FakeResult((1,) if params[0] in self.roles else None)
        if params and "FROM public.platform_subjects" in text:
            return FakeResult((1,) if params[1] in self.accounts else None)
        return FakeResult(None)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def run(connection, *argv):
    with mock.patch("psycopg.connect", return_value=connection):
        return install.main(["--database-url", "postgresql://x", *argv])


def test_logins_only_creates_logins_without_schema_or_grants():
    connection = FakeConnection(roles={"id-wr-agent"})
    assert run(connection, "--logins-only", "--principal", "id-wr-agent=writer",
               "--principal", "id-wr-agent-tools=reader") == 0
    joined = "\n".join(connection.statements)
    assert "CREATE SCHEMA" not in joined
    assert "GRANT" not in joined
    assert joined.count("pgaadauth_create_principal") == 1
    assert joined.count("ALTER ROLE") == 2
    assert connection.committed


def test_existing_logins_installs_schema_and_grants_only():
    connection = FakeConnection(roles={"id-wr-agent"}, accounts={"U1"})
    assert run(connection, "--existing-logins", "--principal", "id-wr-agent=writer",
               "--grant", "data_quality=U1") == 0
    joined = "\n".join(connection.statements)
    assert "CREATE SCHEMA" in joined
    assert 'GRANT "agent_writer" TO "id-wr-agent"' in joined
    assert "pgaadauth_create_principal" not in joined
    assert "ALTER ROLE" not in joined
    assert "agent.service_grants" in joined


def test_existing_logins_stops_when_a_login_is_missing():
    connection = FakeConnection()
    with pytest.raises(SystemExit, match="run --logins-only first"):
        run(connection, "--existing-logins", "--principal", "id-wr-agent=writer")
    assert not connection.committed


def test_logins_only_refuses_grants():
    with pytest.raises(SystemExit):
        run(FakeConnection(), "--logins-only", "--grant", "data_quality=U1")
