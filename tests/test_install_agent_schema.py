import importlib.util
import unittest
from pathlib import Path
from unittest import mock

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


class InstallAgentSchemaTests(unittest.TestCase):
    def test_logins_only_creates_logins_without_schema_or_grants(self):
        connection = FakeConnection(roles={"id-wr-agent"})
        self.assertEqual(0, run(connection, "--logins-only", "--principal", "id-wr-agent=writer",
                                "--principal", "id-wr-agent-tools=reader"))
        joined = "\n".join(connection.statements)
        self.assertNotIn("CREATE SCHEMA", joined)
        self.assertNotIn("GRANT", joined)
        self.assertEqual(1, joined.count("pgaadauth_create_principal"))
        self.assertEqual(2, joined.count("ALTER ROLE"))
        self.assertTrue(connection.committed)

    def test_existing_logins_installs_schema_and_grants_only(self):
        connection = FakeConnection(roles={"id-wr-agent"}, accounts={"U1"})
        self.assertEqual(0, run(connection, "--existing-logins", "--principal", "id-wr-agent=writer",
                                "--grant", "data_quality=U1"))
        joined = "\n".join(connection.statements)
        self.assertIn("CREATE SCHEMA", joined)
        self.assertIn('GRANT "agent_writer" TO "id-wr-agent"', joined)
        self.assertNotIn("pgaadauth_create_principal", joined)
        self.assertNotIn("ALTER ROLE", joined)
        self.assertIn("agent.service_grants", joined)

    def test_existing_logins_stops_when_a_login_is_missing(self):
        connection = FakeConnection()
        with self.assertRaisesRegex(SystemExit, "run --logins-only first"):
            run(connection, "--existing-logins", "--principal", "id-wr-agent=writer")
        self.assertFalse(connection.committed)

    def test_logins_only_refuses_grants(self):
        with self.assertRaises(SystemExit):
            run(FakeConnection(), "--logins-only", "--grant", "data_quality=U1")

    def test_platform_reader_may_read_the_onboarding_and_operations_agents_tables_only(self):
        connection = FakeConnection(roles={"ibkr_stg_app"})
        run(connection, "--existing-logins", "--platform-reader", "ibkr_stg_app")
        self.assertIn('GRANT USAGE ON SCHEMA agent TO "ibkr_stg_app"', connection.statements)
        self.assertIn('GRANT SELECT ON agent.onboarding_reviews, agent.onboarding_term_drafts, '
                      'agent.onboarding_reconciliations, agent.ops_investigations, agent.connection_reports TO "ibkr_stg_app"',
                      connection.statements)
        self.assertTrue(connection.committed)
        with self.assertRaises(SystemExit):
            run(FakeConnection(), "--existing-logins", "--platform-reader", "ibkr_stg_app")  # no such role
        with self.assertRaises(SystemExit):
            run(FakeConnection(), "--logins-only", "--platform-reader", "ibkr_stg_app")

    def test_the_onboarding_job_is_known(self):
        self.assertIn("onboarding", install.JOBS)
