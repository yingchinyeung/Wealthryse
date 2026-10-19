"""The shadow-run tool and its temporary job, without a database (docs/QUANT_ANALYTICS.md, "Shadow
run on a restored copy (Fri 30 Oct)").

The guard refuses the live server, its alias, any host in LIVE_DATABASE_HOSTS, a host that does
not look like a restored copy, and a host that is not --copy-host, before anything connects. The
masking, the timing maths and the log framing of the review are checked on made-up values, and
the Bicep is read as text: manual trigger, no schedule, no role assignment, a required image tag.
tests/test_quant_shadow_run_db.py runs the commands on a local PostgreSQL.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import re
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BICEP = ROOT / "infra" / "staging-quant-shadow-job.bicep"
LIVE = "ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com"
ALIAS = "ibkrv2stg.postgres.database.azure.com"
COPY = "ibkrv2stg-quant-shadow-1030.postgres.database.azure.com"
SECRET = "not-a-real-password-7731"


def _module():
    """scripts/quant_shadow_run.py, loaded once (its dataclasses need it in sys.modules)."""
    name = "quant_shadow_run_under_test"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "quant_shadow_run.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


shadow = _module()


def _url(host: str, database: str = "postgres", extra: str = "") -> str:
    return f"postgresql://shadowadmin:{SECRET}@{host}:5432/{database}?sslmode=require{extra}"


def _resolver(*addresses: str):
    def resolve(host, port, type=0):  # noqa: A002 - socket.getaddrinfo's own name
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port)) for address in addresses]
    return resolve


class GuardTests(unittest.TestCase):
    """Each refusal happens before any connection: connect is replaced by one that fails the test."""

    def setUp(self):
        for name in ("connect", "_open"):
            patcher = patch.object(shadow, name, side_effect=AssertionError("the guard let it connect"))
            self.addCleanup(patcher.stop)
            setattr(self, name, patcher.start())

    def _main(self, url: str | None, copy_host: str, *extra: str, environ: dict | None = None):
        settings = {} if url is None else {"DATABASE_ADMIN_URL": url}
        settings.update(environ or {})
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = shadow.main(["preflight", "--copy-host", copy_host, *extra], environ=settings)
        return code, out.getvalue(), err.getvalue()

    def assertRefused(self, url: str | None, copy_host: str, *extra: str, environ: dict | None = None,
                      reason: str = ""):
        code, out, err = self._main(url, copy_host, *extra, environ=environ)
        self.assertEqual(code, shadow.EXIT_REFUSED)
        self.assertIn("Refused:", err)
        self.assertIn("Nothing was connected", err)
        self.assertIn(reason, err)
        self.assertNotIn(SECRET, out + err, "the URL's password is never printed")
        self.assertEqual(json.loads(out.splitlines()[-1])["status"], "refused")
        self.connect.assert_not_called()
        self._open.assert_not_called()

    def test_the_live_server_is_refused(self):
        self.assertRefused(_url(LIVE), LIVE, reason="live database server")
        # Even when --copy-host names something else, and in upper case with a trailing dot.
        self.assertRefused(_url(LIVE.upper() + "."), COPY, reason="live database server")
        # Its private-link name (same server name) too.
        self.assertRefused(_url("ibkrv2stg-pg-auhecwc4itaoo.private.postgres.database.azure.com"), COPY,
                           reason="live database server")

    def test_the_private_dns_alias_is_refused(self):
        self.assertRefused(_url(ALIAS), ALIAS, reason="live database server")

    def test_hosts_in_the_environment_list_are_refused(self):
        other = "ibkrv11-pg-5rcc62xxqblay.postgres.database.azure.com"
        listed = {"LIVE_DATABASE_HOSTS": f"something-else.example, {other}"}
        self.assertRefused(_url(other), other, environ=listed, reason="live database server")
        # A copy-looking name is refused too when the list says it is live.
        self.assertRefused(_url(COPY), COPY, environ={"LIVE_DATABASE_HOSTS": COPY}, reason="live database server")
        self.assertRefused(_url("ibkrv2stg-restore-1.postgres.database.azure.com"), LIVE,
                           reason="names a live database server")

    def test_a_host_that_is_not_a_restored_copy_is_refused(self):
        for host in ("ibkrv2stg-pg-other.postgres.database.azure.com", "localhost", "shadow.example.com",
                     "ibkrv2stg-shadow.postgres.database.azure.com", "ibkrv2stg-pg.recovery-1.example.com"):
            with self.subTest(host):
                self.assertRefused(_url(host), host, reason="does not look like a restored copy")

    def test_a_host_other_than_copy_host_is_refused(self):
        self.assertRefused(_url("ibkrv2stg-upgrade-recovery-1009.postgres.database.azure.com"), COPY,
                           reason="is not --copy-host")
        # A host= parameter cannot swap the host past the check.
        self.assertRefused(_url(COPY, extra=f"&host={LIVE}"), COPY, reason="live database server")
        self.assertRefused(_url(COPY, extra="&host=ibkrv2stg-restore-2.postgres.database.azure.com"), COPY,
                           reason="is not --copy-host")

    def test_urls_that_could_reach_another_server_are_refused(self):
        self.assertRefused(_url(COPY, extra="&hostaddr=10.50.4.4"), COPY, reason="hostaddr")
        self.assertRefused(_url(COPY), COPY, environ={"PGHOSTADDR": "10.50.4.4"}, reason="hostaddr")
        self.assertRefused(_url(COPY, extra="&service=live"), COPY, reason="service")
        self.assertRefused(_url(f"{COPY},{LIVE}"), COPY, reason="exactly one host")
        self.assertRefused("postgresql:///postgres", COPY, reason="exactly one host")
        self.assertRefused(None, COPY, reason="DATABASE_ADMIN_URL is not set")
        self.assertRefused("host=" + COPY, COPY, reason="postgresql:// URL")
        self.assertRefused(_url(COPY), COPY, environ={"CONTAINER_APP_JOB_NAME": "ibkrv2stg-bootstrap"},
                           reason="bootstrap")

    def test_the_url_can_come_from_another_variable(self):
        code, _, err = self._main(None, COPY, "--url-env", "SHADOW_URL", environ={"SHADOW_URL": _url(LIVE)})
        self.assertEqual(code, shadow.EXIT_REFUSED)
        self.assertIn("live database server", err)

    def test_the_copy_subnet_is_checked_on_the_resolved_addresses(self):
        target = shadow.check_target(_url(COPY), COPY, environ={}, copy_subnet="10.50.11.0/27",
                                     resolve=_resolver("10.50.11.4"))
        self.assertEqual((target.host, target.database), (COPY, "postgres"))
        for addresses in (("10.50.4.4",), ("10.50.11.4", "10.50.4.4"), ()):
            with self.subTest(addresses), self.assertRaisesRegex(shadow.Refused, "outside --copy-subnet"):
                shadow.check_target(_url(COPY), COPY, environ={}, copy_subnet="10.50.11.0/27",
                                    resolve=_resolver(*addresses))

        def broken(*args, **kwargs):
            raise socket.gaierror("no such host")

        with self.assertRaisesRegex(shadow.Refused, "could not be resolved"):
            shadow.check_target(_url(COPY), COPY, environ={}, copy_subnet="10.50.11.0/27", resolve=broken)
        with self.assertRaisesRegex(shadow.Refused, "is not a subnet"):
            shadow.check_target(_url(COPY), COPY, environ={}, copy_subnet="recovery", resolve=broken)
        # Through the command line, with the resolver the guard uses.
        with patch.object(shadow.socket, "getaddrinfo", _resolver("10.50.4.4")):
            self.assertRefused(_url(COPY), COPY, "--copy-subnet", "10.50.11.0/27", reason="outside --copy-subnet")

    def test_a_copy_passes_and_the_host_and_database_are_printed_first(self):
        self._open.side_effect = ConnectionError("unreachable from here")
        for host in (COPY, "ibkrv2stg-upgrade-recovery-1009.postgres.database.azure.com",
                     "ibkrv2stg-pg-restore-1030.postgres.database.azure.com"):
            with self.subTest(host):
                self._open.reset_mock()
                code, out, err = self._main(_url(host, "ibkr_platform"), host.upper())
                self.assertEqual(code, shadow.EXIT_REFUSED)  # could not connect
                self.assertIn(f"Restored copy: host {host}, database ibkr_platform (preflight)", out)
                self.assertIn("Could not connect", err)
                self.assertNotIn(SECRET, out + err)
                self._open.assert_called_once()
                self.assertEqual(self._open.call_args.args[0].host, host)
        self.connect.assert_not_called()

    def test_a_command_that_fails_on_the_copy_is_reported_masked(self):
        class Connection:
            closed = False

            def close(self):
                self.closed = True

        connection = Connection()
        self._open.side_effect = None
        self._open.return_value = connection

        def broken(*args):
            raise RuntimeError("could not read subject U9100001")

        err = io.StringIO()
        with patch.dict(shadow.COMMANDS, {"preflight": broken}), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(err):
            code = shadow.main(["preflight", "--copy-host", COPY], environ={"DATABASE_ADMIN_URL": _url(COPY)})
        self.assertEqual(code, shadow.EXIT_FAILED)
        self.assertIn("preflight failed on the copy", err.getvalue())
        self.assertIn("RuntimeError: could not read subject u??", err.getvalue())
        self.assertNotIn("U9100001", err.getvalue())
        self.assertTrue(connection.closed)

    def test_every_command_runs_the_guard(self):
        for command in shadow.COMMANDS:
            with self.subTest(command):
                out, err = io.StringIO(), io.StringIO()
                extra = ["--out", "/nonexistent/review"] if command == "review" else []
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = shadow.main([command, "--copy-host", ALIAS, *extra],
                                       environ={"DATABASE_ADMIN_URL": _url(ALIAS)})
                self.assertEqual(code, shadow.EXIT_REFUSED)
                self.assertIn("live database server", err.getvalue())
        self._open.assert_not_called()


class MaskingTests(unittest.TestCase):
    def test_labels_are_sorted_and_stable(self):
        masker = shadow.Masker(["U9100002", "U9100001", "alpha-world-ms", None, ""])
        self.assertEqual(masker.labels, {"U9100001": "u01", "U9100002": "u02", "alpha-world-ms": "u03"})
        self.assertEqual(shadow.Masker(["U9100001", "U9100002", "alpha-world-ms"]).labels, masker.labels)
        self.assertEqual(masker.label("U7000001"), shadow.UNKNOWN_ID)
        self.assertEqual(len(set(shadow.Masker(f"U{n:07d}" for n in range(120)).labels.values())), 120)
        self.assertEqual(shadow.Masker(f"U{n:07d}" for n in range(120)).label("U0000000"), "u001")

    def test_text_masks_ids_only(self):
        masker = shadow.Masker(["U9100001", "alpha-world-ms"])
        line = masker.text("quant section failed subject=U9100001 as_of=2026-10-16 (U9100001x, DU1234567, "
                           "alpha-world-ms) 0.25")
        self.assertEqual(line, "quant section failed subject=u01 as_of=2026-10-16 (u01x, u??, u02) 0.25")

    def test_clean_replaces_dates_and_numbers_and_keeps_labels(self):
        masker = shadow.Masker(["U9100001", "U9100002"])
        cleaned = masker.clean("ValueError: U9100002 needs 252 returns on 2026-10-16T09:30:00Z, has 17 "
                               "(NAV 1,234,567.89 | -0.0123 1e-05) for U9300009\n  next")
        self.assertEqual(cleaned, "ValueError: u02 needs <n> returns on <date>, has <n> (NAV <n> / -<n> <n>) "
                                  "for u?? next")
        self.assertEqual(masker.clean("café ≤ U9100001"), "caf? ? u01")
        self.assertEqual(len(masker.clean("x" * 1000)), 300)
        self.assertEqual(masker.clean(None), "")

    def test_the_log_formatter_masks_ids(self):
        shadow._MASK[0] = shadow.Masker(["U9100001"])
        self.addCleanup(shadow._MASK.__setitem__, 0, shadow.Masker())
        record = shadow.logging.LogRecord("ibkr_etl.quant_snapshots", 40, __file__, 1,
                                          "quant section failed subject=%s", ("U9100001",), None)
        self.assertEqual(shadow._MaskedFormatter("%(message)s").format(record), "quant section failed subject=u01")


class TimingTests(unittest.TestCase):
    def test_percentiles_by_linear_interpolation(self):
        self.assertEqual(shadow.percentile([10, 1, 3, 2, 4], 0.5), 3)
        self.assertAlmostEqual(shadow.percentile([1, 2, 3, 4, 10], 0.95), 8.8)
        self.assertEqual(shadow.percentile([7], 0.95), 7)
        self.assertIsNone(shadow.percentile([], 0.5))
        self.assertEqual(shadow.spread([1, 2, 3, 4, 10, None]), {"count": 5, "p50": 3.0, "p95": 8.8, "max": 10.0})
        self.assertEqual(shadow.spread([]), {"count": 0, "p50": None, "p95": None, "max": None})

    def test_the_window_verdict(self):
        fits = shadow.window_verdict(300.0, subject_dates=12, skipped_for_time=0, minutes=8)
        self.assertEqual((fits["would_end_at"], fits["fits_window"], fits["move_to_10_00"], fits["within_budget"],
                          fits["margin_seconds"]), ("09:35:00", True, False, True, 300.0))
        edge = shadow.window_verdict(600.0, subject_dates=12, skipped_for_time=0, minutes=8)
        self.assertEqual((edge["would_end_at"], edge["fits_window"], edge["within_budget"]), ("09:40:00", True, False))
        late = shadow.window_verdict(612.4, subject_dates=12, skipped_for_time=0, minutes=8)
        self.assertEqual((late["would_end_at"], late["fits_window"], late["move_to_10_00"], late["margin_seconds"]),
                         ("09:40:13", False, True, -12.4))
        # Anything skipped for time means the window is too short, however quick the rest was.
        skipped = shadow.window_verdict(480.5, subject_dates=12, skipped_for_time=2, minutes=8)
        self.assertEqual((skipped["fits_window"], skipped["move_to_10_00"]), (False, True))
        self.assertFalse(shadow.window_verdict(None, subject_dates=0, skipped_for_time=0, minutes=8)["measured"])

    def test_the_timing_summary_uses_cold_builds_and_the_first_cold_pass(self):
        def sections(status="rebuilt", build_ms=100.0):
            return {name: {"status": status, "build_ms": build_ms if status == "rebuilt" else None}
                    for name in shadow.qs.SECTION_NAMES}

        passes = [
            {"pass_id": "a", "phase": "latest", "started_at": "2026-10-30 11:05", "finished": True, "seconds": 245.5,
             "summary": {"cold": True, "subject_dates": 3, "rebuilt": 3, "unchanged": 0, "minutes": 8,
                         "skipped_for_time": [], "runner": "job", "cpu_count": 2}},
            {"pass_id": "b", "phase": "latest", "started_at": "2026-10-30 12:05", "finished": True, "seconds": 20.0,
             "summary": {"cold": False, "subject_dates": 3, "unchanged": 3, "minutes": 8, "skipped_for_time": []}},
            {"pass_id": "c", "phase": "history", "started_at": "2026-10-30 11:10", "finished": True, "seconds": 1000.0,
             "summary": {}},
        ]
        builds = [
            {"pass_id": "a", "phase": "latest", "status": "rebuilt", "seconds": 1.0, "load_ms": 200.0,
             "sections": sections(build_ms=50.0)},
            {"pass_id": "a", "phase": "latest", "status": "rebuilt", "seconds": 2.0, "load_ms": 300.0,
             "sections": sections(build_ms=150.0)},
            {"pass_id": "a", "phase": "latest", "status": "rebuilt", "seconds": 3.0, "load_ms": 400.0,
             "sections": {**sections(), "tail": {"status": "error"}}},
            {"pass_id": "c", "phase": "history", "status": "rebuilt", "seconds": 4.0, "load_ms": 500.0,
             "sections": sections(build_ms=250.0)},
            # Partly unchanged and unchanged builds are not cold, so they are not timed.
            {"pass_id": "c", "phase": "history", "status": "rebuilt", "seconds": 0.1, "load_ms": 5.0,
             "sections": {**sections("unchanged"), "execution": {"status": "rebuilt", "build_ms": 9.0}}},
            {"pass_id": "b", "phase": "latest", "status": "unchanged", "seconds": 0.2, "sections": sections("unchanged")},
            {"pass_id": "c", "phase": "history", "status": "failed", "seconds": None, "sections": {}},
        ]
        timing = shadow.timing_summary(builds, passes)
        self.assertEqual(timing["per_subject_date_seconds"], {"count": 4, "p50": 2.5, "p95": 3.85, "max": 4.0})
        self.assertEqual(timing["load_ms"], {"count": 4, "p50": 350.0, "p95": 485.0, "max": 500.0})
        self.assertEqual(timing["per_section_ms"]["skill"], {"count": 4, "p50": 125.0, "p95": 235.0, "max": 250.0})
        self.assertEqual(timing["per_section_ms"]["tail"]["count"], 3)  # the error is not a timing
        latest = timing["latest_pass"]
        self.assertEqual((latest["seconds"], latest["would_end_at"], latest["fits_window"], latest["cold"],
                          latest["runner"]), (245.5, "09:34:06", True, True, "job"))
        self.assertEqual(latest["per_subject_date_seconds"], {"count": 3, "p50": 2.0, "p95": 2.9, "max": 3.0})
        self.assertEqual(timing["history"], {"passes": 1, "seconds": 1000.0, "rebuilt": 2})
        self.assertEqual(timing["latest_passes"], 2)
        # Without a cold pass the last finished one is used, and says so.
        warm = shadow.timing_summary(builds[5:6], passes[1:2])["latest_pass"]
        self.assertEqual((warm["cold"], warm["seconds"], warm["per_subject_date_seconds"]["count"]), (False, 20.0, 0))
        self.assertFalse(shadow.timing_summary([], [])["latest_pass"]["measured"])


class ReviewFramingTests(unittest.TestCase):
    def test_the_review_lines_put_the_file_back_together(self):
        text = "# Review\n\n| a | b |\n|---|---|\n| u01 | 3 |\n"
        lines: list[str] = []
        shadow._emit("md", text, out=lines.append)
        self.assertEqual(lines[0], f"QUANT_SHADOW_REVIEW md BEGIN sha256={hashlib.sha256(text.encode()).hexdigest()} "
                                   "lines=5")
        self.assertEqual(lines[-1], "QUANT_SHADOW_REVIEW md END")
        body = [re.match(r"QUANT_SHADOW_REVIEW md (\d{5}) (.*)$", line) for line in lines[1:-1]]
        rebuilt = "\n".join(match.group(2) for match in sorted(body, key=lambda m: m.group(1))) + "\n"
        self.assertEqual(rebuilt, text)
        # An empty line keeps its number and its separating space, so a log query can match it.
        self.assertEqual(lines[2], "QUANT_SHADOW_REVIEW md 00002 ")


class BicepTests(unittest.TestCase):
    text = BICEP.read_text(encoding="utf-8")

    def test_it_is_started_by_hand_only(self):
        self.assertIn("triggerType: 'Manual'", self.text)
        self.assertIn("manualTriggerConfig", self.text)
        self.assertNotIn("scheduleTriggerConfig", self.text)
        self.assertNotIn("cronExpression", self.text)
        self.assertNotIn("eventTriggerConfig", self.text)
        self.assertIn("replicaRetryLimit: 0", self.text)

    def test_it_assigns_no_roles_and_creates_nothing_else(self):
        self.assertNotIn("roleAssignments", self.text)
        self.assertNotIn("roleDefinition", self.text)
        resources = re.findall(r"^resource \w+ '([^']+)'( existing)?", self.text, flags=re.MULTILINE)
        created = [kind for kind, existing in resources if not existing]
        self.assertEqual(created, ["Microsoft.App/jobs@2024-03-01"])
        self.assertIn("param identityResourceId string\n", self.text)

    def test_the_image_tag_is_required(self):
        self.assertRegex(self.text, r"@minLength\(1\)\nparam imageTag string\n")
        self.assertIn("/ibkr-platform:${imageTag}'", self.text)

    def test_the_copy_url_comes_from_the_named_secret(self):
        self.assertRegex(self.text, r"param copyUrlSecretName string\n")
        self.assertIn("keyVaultUrl: '${vault.properties.vaultUri}secrets/${copyUrlSecretName}'", self.text)
        self.assertIn("{ name: 'DATABASE_ADMIN_URL', secretRef: 'copy-admin-url' }", self.text)
        # Never the live URLs, and never the daily job's switches.
        for name in ("database-admin-url'", "database-app-url", "'DATABASE_URL'", "QUANT_ANALYTICS_BUILD",
                     "LINEAGE_RECORDING", "PUBLISH_GATE_MODE"):
            self.assertNotIn(name, self.text)

    def test_the_timeout_is_long_and_the_size_is_the_daily_jobs(self):
        self.assertRegex(self.text, r"@minValue\(3600\)\n@maxValue\(\d+\)\nparam replicaTimeoutSeconds int = (\d+)")
        default = int(re.search(r"param replicaTimeoutSeconds int = (\d+)", self.text).group(1))
        self.assertGreaterEqual(default, 4 * 3600)
        self.assertIn("replicaTimeout: replicaTimeoutSeconds", self.text)
        daily = (ROOT / "infra" / "staging-quant-analytics-job.bicep").read_text(encoding="utf-8")
        for setting in ("cpu: json('0.5')", "memory: '1Gi'", "{ name: 'QUANT_ANALYTICS_MINUTES', value: '8' }"):
            self.assertIn(setting, daily)
            self.assertIn(setting, self.text)

    def test_the_args_run_the_four_steps_against_the_copy(self):
        script = re.search(r"'''(.*?)'''", self.text, flags=re.DOTALL).group(1)
        steps = re.findall(r"python \$s ([a-z-]+)", script)
        self.assertEqual(steps, ["preflight", "apply-held", "run", "review"])
        self.assertNotIn("undo", script)
        self.assertEqual(script.count("$c"), 4, "every step passes --copy-host and --copy-subnet")
        self.assertIn('c="--copy-host $QUANT_SHADOW_COPY_HOST --copy-subnet $QUANT_SHADOW_COPY_SUBNET"', script)
        self.assertNotIn("bootstrap", script)
        for name in ("QUANT_SHADOW_COPY_HOST", "QUANT_SHADOW_COPY_SUBNET", "QUANT_SHADOW_MAX_MINUTES",
                     "LIVE_DATABASE_HOSTS"):
            self.assertIn(f"{{ name: '{name}', value: ", self.text)
        hosts = re.search(r"param liveDatabaseHosts string = '([^']+)'", self.text).group(1).split(",")
        self.assertLessEqual(set(shadow.LIVE_HOSTS), set(hosts))

    def test_it_is_not_the_bootstrap_job(self):
        self.assertIn("param jobName string = 'ibkrv2stg-quant-shadow'", self.text)
        self.assertNotIn("bootstrap_postgres", self.text)


if __name__ == "__main__":
    unittest.main()
