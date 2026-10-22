import json
import unittest

from ibkr_etl import report_profiles as profiles
from ibkr_etl.subject_onboarding import (
    approved_reference_definitions, configuration_sha256, fixed_unit_composite_definition,
)


def composite(**changes):
    fields = dict(
        subject_id="alpha-world-asia", display_name="Alpha World Asia", base_currency="HKD",
        normalization_date="2025-03-03", constant_cash=58.0,
        components=[
            {"component_id": "hong-kong-china", "component_name": "China & Hong Kong", "component_type": "portfolio",
             "coefficient": 0.91, "source_currency": "USD", "price_convention": "nav"},
            {"component_id": "hang-seng-price-index", "component_name": "Hang Seng Index price index",
             "component_type": "benchmark", "coefficient": -0.49, "source_currency": "HKD",
             "price_convention": "price_index_close"},
        ],
    )
    fields.update(changes)
    return fixed_unit_composite_definition(**fields)


DESCRIPTION = "Long China and Hong Kong, hedged with the Hang Seng Index."


class BuiltInProfileTests(unittest.TestCase):
    def test_ms_and_t_keep_their_own_methodology(self):
        ms = profiles.built_in_profile("alpha-world-ms")
        t = profiles.built_in_profile("alpha-world-t")
        self.assertTrue(ms.built_in and t.built_in)
        self.assertEqual((ms.style, ms.inception, ms.reporting_currency, ms.markets),
                         ("alpha-world-ms", "2025-01-20", "HKD", ("XNYS", "XHKG")))
        self.assertEqual(t.markets, ("XNYS", "XHKG", "XSHG", "XTAI"))
        self.assertIsNone(ms.description)
        self.assertIsNone(ms.composition)
        self.assertIsNone(profiles.built_in_profile("U0000001"))

    def test_style_follows_the_snapshot_and_ms_t_ignore_any_profile_they_carry(self):
        self.assertEqual(profiles.report_style("alpha-world-t"), "alpha-world-t")
        self.assertIsNone(profiles.report_style("hong-kong-china"))
        self.assertEqual(profiles.report_style({"subject_id": "alpha-world-ms",
                                                "report_profile": {"style": "alpha-world-t"}}), "alpha-world-ms")
        self.assertIsNone(profiles.report_style({"subject_id": "U0000002"}))


class CompositeProfileTests(unittest.TestCase):
    def test_profile_carries_name_description_tree_and_definition_fingerprint(self):
        definition = composite()
        record = profiles.build_profile(definition=definition, style="alpha-world-t", description=DESCRIPTION)
        self.assertEqual(record["display_name"], "Alpha World Asia")
        self.assertEqual(record["description"], DESCRIPTION)
        self.assertEqual(record["inception"], "2025-03-03")
        self.assertEqual(record["markets"], ["XNYS", "XHKG", "XSHG", "XTAI"])
        self.assertEqual(record["definition_sha256"], configuration_sha256(definition))
        groups = {group["label"]: group for group in record["composition"]["groups"]}
        self.assertEqual([item["name"] for item in groups["Long"]["items"]], ["China & Hong Kong"])
        self.assertAlmostEqual(groups["Cash"]["share"], 100 * 58 / (58 + 91 - 49))
        profile = profiles.profile_from_metadata("alpha-world-asia", json.dumps({"report_profile": record}),
                                                 definition_sha256=configuration_sha256(definition))
        self.assertFalse(profile.built_in)
        self.assertEqual((profile.style, profile.description), ("alpha-world-t", DESCRIPTION))
        snapshot = {"subject_id": "alpha-world-asia", "report_profile": profile.as_dict()}
        self.assertEqual(profiles.snapshot_profile(snapshot), profile)
        self.assertEqual(profiles.report_style(snapshot), "alpha-world-t")

    def test_a_copy_of_ms_gets_a_profile_but_ms_itself_does_not(self):
        reference = dict(approved_reference_definitions()["alpha-world-ms"])
        with self.assertRaisesRegex(profiles.ReportProfileError, "keep their own"):
            profiles.build_profile(definition=reference, style="alpha-world-ms", description=DESCRIPTION)
        copy = composite(subject_id="alpha-world-ms-2", display_name="Alpha World MS II",
                         normalization_date="2025-01-20")
        self.assertEqual(profiles.build_profile(definition=copy, style="alpha-world-ms",
                                                description=DESCRIPTION)["style"], "alpha-world-ms")

    def test_only_hkd_composites_with_a_described_purpose_qualify(self):
        with self.assertRaisesRegex(profiles.ReportProfileError, "HKD"):
            profiles.build_profile(definition=composite(base_currency="USD"), style="alpha-world-ms",
                                   description=DESCRIPTION)
        with self.assertRaisesRegex(profiles.ReportProfileError, "10 to 600"):
            profiles.build_profile(definition=composite(), style="alpha-world-ms", description="Short")
        with self.assertRaisesRegex(profiles.ReportProfileError, "MS or Alpha World T"):
            profiles.build_profile(definition=composite(), style="standard", description=DESCRIPTION)
        with self.assertRaisesRegex(profiles.ReportProfileError, "choose one"):
            profiles.check_style(composite(), None)
        self.assertIsNone(profiles.check_style(composite(base_currency="USD"), None))
        self.assertEqual(profiles.check_style(composite(), "alpha-world-t"), "alpha-world-t")
        filtered = dict(approved_reference_definitions()["hong-kong-china"])
        with self.assertRaisesRegex(profiles.ReportProfileError, "fixed-unit composites"):
            profiles.build_profile(definition=filtered, style="alpha-world-ms", description=DESCRIPTION)

    def test_edited_or_rebound_profiles_are_refused(self):
        definition = composite()
        record = profiles.build_profile(definition=definition, style="alpha-world-ms", description=DESCRIPTION)
        edited = {**record, "description": "Something the owner never approved."}
        with self.assertRaisesRegex(profiles.ReportProfileError, "integrity"):
            profiles.profile_from_metadata("alpha-world-asia", {"report_profile": edited})
        with self.assertRaisesRegex(profiles.ReportProfileError, "different definition"):
            profiles.profile_from_metadata("alpha-world-asia", {"report_profile": record},
                                           definition_sha256="0" * 64)
        self.assertIsNone(profiles.profile_from_metadata("hong-kong-china", {"description": "x"}))

    def test_a_snapshot_profile_must_name_its_own_subject(self):
        record = profiles.build_profile(definition=composite(), style="alpha-world-ms", description=DESCRIPTION)
        profile = profiles.profile_from_metadata("alpha-world-asia", {"report_profile": record})
        with self.assertRaisesRegex(profiles.ReportProfileError, "another subject"):
            profiles.snapshot_profile({"subject_id": "alpha-world-other", "report_profile": profile.as_dict()})


class LoadProfileTests(unittest.TestCase):
    class Connection:
        def __init__(self, row):
            self.row, self.statements = row, []

        def execute(self, statement, parameters=()):
            self.statements.append((statement, parameters))
            row = self.row
            return type("Rows", (), {"fetchone": lambda _self: row})()

    def test_profile_is_bound_to_the_current_approved_definition(self):
        definition = composite()
        record = profiles.build_profile(definition=definition, style="alpha-world-ms", description=DESCRIPTION)
        row = {"metadata_json": {"report_profile": record}, "reporting_currency": "HKD",
               "configuration_sha256": configuration_sha256(definition)}
        connection = self.Connection(row)
        profile = profiles.load_profile(connection, "alpha-world-asia", tenant_id="tenant")
        self.assertEqual(profile.display_name, "Alpha World Asia")
        self.assertEqual(connection.statements[0][1], ("tenant", "alpha-world-asia"))
        with self.assertRaisesRegex(profiles.ReportProfileError, "no approved definition"):
            profiles.load_profile(self.Connection({**row, "configuration_sha256": None}), "alpha-world-asia")
        with self.assertRaisesRegex(profiles.ReportProfileError, "different definition"):
            profiles.load_profile(self.Connection({**row, "configuration_sha256": "1" * 64}), "alpha-world-asia")
        self.assertEqual(profiles.load_profile(self.Connection(None), "alpha-world-ms").style, "alpha-world-ms")
        self.assertIsNone(profiles.load_profile(self.Connection(None), "missing"))


if __name__ == "__main__":
    unittest.main()
