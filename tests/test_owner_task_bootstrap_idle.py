"""Local paginated idle-inventory acceptance; no cloud, dispatch or main call."""
from pathlib import Path
import sys
import unittest
from unittest.mock import call, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import resume_owner_task_postgres_trial as resume

BASE = resume.deploy.PATH + "/executions"
FIRST = BASE + "?api-version=2024-03-01"


def continuation(number=1, **changes):
    values = {"host": "management.azure.com", "path": BASE,
              "version": "2024-03-01", "scheme": "https"}
    values.update(changes)
    return f"{values['scheme']}://{values['host']}{values['path']}?api-version={values['version']}&skiptoken={number}"


def page(*statuses, next_link=None):
    result = {"value": [{"properties": {"status": status}} for status in statuses]}
    if next_link is not None:
        result["nextLink"] = next_link
    return result


class BootstrapIdleTests(unittest.TestCase):
    def deny(self, pages):
        with patch.object(resume.deploy, "request", side_effect=pages) as request:
            with self.assertRaises(ValueError):
                resume.assert_bootstrap_idle()
        return request

    def test_terminal_first_page_is_idle(self):
        with patch.object(resume.deploy, "request", return_value=page("Succeeded", "Failed", "Stopped")) as request:
            self.assertIsNone(resume.assert_bootstrap_idle())
        request.assert_called_once_with("GET", FIRST)

    def test_empty_inventory_is_idle_when_value_list_is_present(self):
        with patch.object(resume.deploy, "request", return_value={"value": []}):
            self.assertIsNone(resume.assert_bootstrap_idle())

    def test_terminal_pages_are_fully_traversed(self):
        pages = [page("Succeeded", next_link=continuation(1)), page("Failed", next_link=continuation(2)), page("Stopped")]
        with patch.object(resume.deploy, "request", side_effect=pages) as request:
            self.assertIsNone(resume.assert_bootstrap_idle())
        self.assertEqual(request.call_args_list, [call("GET", FIRST),
            call("GET", BASE + "?api-version=2024-03-01&skiptoken=1"),
            call("GET", BASE + "?api-version=2024-03-01&skiptoken=2")])

    def test_busy_second_page_blocks_new_dispatch(self):
        request = self.deny([page("Succeeded", next_link=continuation()), page("Running")])
        self.assertEqual(request.call_count, 2)

    def test_any_unknown_status_fails_closed(self):
        for value in ("Running", "Pending", "Processing", "StoppedByUser", "succeeded", "", None, False, 0):
            with self.subTest(status=value):
                self.deny([page(value)])

    def test_missing_value_is_not_an_empty_successful_inventory(self):
        self.deny([{}])

    def test_malformed_value_types_are_denied(self):
        for value in (None, {}, (), "[]", 0, False):
            with self.subTest(value=value):
                self.deny([{"value": value}])

    def test_non_dictionary_page_is_denied(self):
        for value in (None, [], "{}", False):
            with self.subTest(value=value):
                self.deny([value])

    def test_malformed_run_and_properties_are_denied(self):
        for run in (None, [], "run", False, {}, {"properties": None}, {"properties": []}, {"properties": "Succeeded"}):
            with self.subTest(run=run):
                self.deny([{"value": [run]}])

    def test_missing_status_is_denied(self):
        self.deny([{"value": [{"properties": {}}]}])

    def test_malformed_second_page_does_not_claim_idle(self):
        for bad in ({}, {"value": None}, {"value": [{"properties": {}}]}):
            with self.subTest(page=bad):
                request = self.deny([page("Succeeded", next_link=continuation()), bad])
                self.assertEqual(request.call_count, 2)

    def test_nonstring_continuations_even_falsey_are_denied(self):
        for value in (0, False, [], {}, 1, True):
            with self.subTest(link=value):
                self.deny([dict(page("Succeeded"), nextLink=value)])

    def test_missing_none_or_empty_continuation_is_terminal(self):
        for value in ({"value": []}, {"value": [], "nextLink": None}, {"value": [], "nextLink": ""}):
            with self.subTest(page=value), patch.object(resume.deploy, "request", return_value=value) as request:
                self.assertIsNone(resume.assert_bootstrap_idle())
            self.assertEqual(request.call_count, 1)

    def test_continuation_other_host_port_or_credentials_denied(self):
        for host in ("evil.example", "management.azure.com.evil.example", "management.azure.com:443",
                     "user@management.azure.com", "MANAGEMENT.AZURE.COM"):
            with self.subTest(host=host):
                self.deny([page("Succeeded", next_link=continuation(host=host))])

    def test_continuation_different_path_or_resource_denied(self):
        for path in (BASE + "/other", BASE.replace("ibkrv2stg-bootstrap", "another-job"),
                     BASE + "/", BASE.replace("/executions", "")):
            with self.subTest(path=path):
                self.deny([page("Succeeded", next_link=continuation(path=path))])

    def test_continuation_wrong_missing_or_duplicate_api_version_denied(self):
        links = (continuation(version="2025-01-01"), "https://management.azure.com" + BASE + "?skiptoken=1",
                 continuation() + "&api-version=2024-03-01", continuation(version=""))
        for link in links:
            with self.subTest(link=link):
                self.deny([page("Succeeded", next_link=link)])

    def test_continuation_scheme_fragment_or_relative_target_denied(self):
        for link in (continuation(scheme="http"), continuation() + "#fragment", FIRST, "/executions?api-version=2024-03-01"):
            with self.subTest(link=link):
                self.deny([page("Succeeded", next_link=link)])

    def test_repeated_initial_link_is_denied_before_second_request(self):
        request = self.deny([page("Succeeded", next_link="https://management.azure.com" + FIRST)])
        self.assertEqual(request.call_count, 1)

    def test_repeated_followup_link_is_denied_before_refetch(self):
        request = self.deny([page("Succeeded", next_link=continuation()), page("Stopped", next_link=continuation())])
        self.assertEqual(request.call_count, 2)

    def test_continuation_cycle_is_denied_before_revisiting(self):
        request = self.deny([page("Succeeded", next_link=continuation(1)),
            page("Stopped", next_link=continuation(2)), page("Failed", next_link=continuation(1))])
        self.assertEqual(request.call_count, 3)

    def test_one_hundred_terminal_pages_are_accepted(self):
        pages = [page("Succeeded", next_link=continuation(i + 1)) for i in range(99)] + [page("Stopped")]
        with patch.object(resume.deploy, "request", side_effect=pages) as request:
            self.assertIsNone(resume.assert_bootstrap_idle())
        self.assertEqual(request.call_count, 100)

    def test_one_hundred_page_bound_blocks_the_101st_request(self):
        request = self.deny([page("Succeeded", next_link=continuation(i + 1)) for i in range(100)])
        self.assertEqual(request.call_count, 100)


if __name__ == "__main__":
    unittest.main()
