"""Renderer layout names must match what the deployed assurance job expects.

deploy/report-layouts.json records the names the deployed assurance image was
tested with. Changing a layout name in the renderer without rebuilding and testing
the assurance image made assurance fail on 13 Oct 2026; this test stops that in CI.
"""
import importlib.util
import json
import unittest
from pathlib import Path

from ibkr_etl import monthly_artifacts, portfolio_assurance, presentation_design

ROOT = Path(__file__).resolve().parents[1]
LAYOUTS = json.loads((ROOT / "deploy" / "report-layouts.json").read_text())
IMAGES = json.loads((ROOT / "deploy" / "deployed-images.json").read_text())["images"]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReportLayoutGuardTest(unittest.TestCase):
    def test_renderer_layout_names_match_the_deployed_assurance_job(self):
        for subject, names in LAYOUTS["subjects"].items():
            with self.subTest(subject=subject):
                self.assertEqual(monthly_artifacts.factsheet_layout_version(subject), names["factsheet"],
                                 "Factsheet layout changed: rebuild and test the assurance image, then update deploy/report-layouts.json")
                self.assertEqual(presentation_design.presentation_content_contract_version(subject), names["presentation"],
                                 "Presentation contract changed: rebuild and test the assurance image, then update deploy/report-layouts.json")

    def test_assurance_checks_the_same_names_the_renderer_writes(self):
        for subject in LAYOUTS["subjects"]:
            self.assertEqual(portfolio_assurance.factsheet_layout_version(subject),
                             monthly_artifacts.factsheet_layout_version(subject))
            self.assertEqual(portfolio_assurance.presentation_content_contract_version(subject),
                             presentation_design.presentation_content_contract_version(subject))

    def test_assurance_image_build_expects_the_recorded_names(self):
        release = load("website_release_layout_guard", ROOT / "deploy" / "website_release.py")
        for subject, names in LAYOUTS["subjects"].items():
            if f"'{subject}'" in release.ASSURANCE_CHECK:
                self.assertIn(f"'{subject}': '{names['factsheet']}'", release.ASSURANCE_CHECK)
                self.assertIn(names["presentation"], release.ASSURANCE_CHECK)

    def test_recorded_jobs_match_the_image_record(self):
        self.assertEqual(IMAGES[LAYOUTS["assurance_job"]], LAYOUTS["assurance_image"])
        self.assertEqual(IMAGES[LAYOUTS["month_end_job"]], LAYOUTS["month_end_image"])


class DriftCompareTest(unittest.TestCase):
    drift = load("drift_check_guard", ROOT / "deploy" / "drift_check.py")
    WEB = "ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform:web-" + "a" * 40

    def live(self):
        return {**IMAGES, "ibkrv2stg-web": self.WEB}

    def test_matching_images_have_no_drift(self):
        self.assertEqual(self.drift.compare(self.live(), IMAGES, LAYOUTS, in_main=lambda c: True), [])

    def test_changed_new_and_missing_images_are_reported(self):
        live = self.live()
        live["ibkrv2stg-derived-daily"] = "other@sha256:" + "b" * 64
        live["ibkrv2stg-new-job"] = "new@sha256:" + "c" * 64
        del live["ibkrv2stg-bootstrap"]
        problems = "\n".join(self.drift.compare(live, IMAGES, LAYOUTS, in_main=lambda c: True))
        self.assertIn("ibkrv2stg-derived-daily: runs other", problems)
        self.assertIn("ibkrv2stg-new-job: deployed but not recorded", problems)
        self.assertIn("ibkrv2stg-bootstrap: recorded but no longer deployed", problems)

    def test_website_must_be_a_main_build(self):
        live = self.live()
        self.assertIn("not in main", self.drift.compare(live, IMAGES, LAYOUTS, in_main=lambda c: False)[0])
        live["ibkrv2stg-web"] = "ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:" + "d" * 64
        self.assertIn("not a web-<commit> build", self.drift.compare(live, IMAGES, LAYOUTS, in_main=lambda c: True)[0])

    def test_assurance_image_change_is_reported_against_the_layout_record(self):
        live = self.live()
        recorded = {**IMAGES, LAYOUTS["assurance_job"]: "new@sha256:" + "e" * 64}
        live[LAYOUTS["assurance_job"]] = recorded[LAYOUTS["assurance_job"]]
        problems = self.drift.compare(live, recorded, LAYOUTS, in_main=lambda c: True)
        self.assertEqual(len(problems), 1)
        self.assertIn("report-layouts.json was checked against", problems[0])


if __name__ == "__main__":
    unittest.main()
