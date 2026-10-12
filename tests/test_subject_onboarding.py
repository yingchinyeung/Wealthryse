from __future__ import annotations

import unittest
from pathlib import Path

from ibkr_etl.subject_onboarding import (
    POST_WAVE0_ONBOARDING_SEQUENCE,
    Dependency,
    approved_reference_definitions,
    configuration_sha256,
    dependencies_for_definition,
    derived_portfolio_studio_templates,
    filtered_portfolio_definition,
    fixed_unit_composite_definition,
    master_account_definition,
    onboarding_branch_catalog,
    reference_definition_summary,
    reference_onboarding_programme,
    topological_refresh_order,
    validate_reference_runtime_contract,
    validate_typed_runtime_contract,
)


class SubjectOnboardingTests(unittest.TestCase):
    def test_reference_definitions_preserve_approved_formulas(self) -> None:
        definitions = approved_reference_definitions()
        hk = definitions["hong-kong-china"]
        self.assertEqual(hk["source_account_id"], "U16088606")
        self.assertEqual(hk["cash_policy"]["opening_cash"]["HKD"], 484300.0)
        self.assertEqual(hk["cash_policy"]["opening_cash"]["USD"], 0.0)
        self.assertEqual(hk["cash_policy"]["allowed_currencies"], ["HKD", "USD"])
        self.assertIn("TWD", hk["position_filter"]["excluded_currencies"])

        ms = definitions["alpha-world-ms"]
        t = definitions["alpha-world-t"]
        self.assertEqual(ms["initial_exposure"]["nav"], 100.0)
        self.assertEqual(ms["initial_exposure"]["gross_risky"], 230.0)
        self.assertEqual(ms["components"][0]["component_id"], "hong-kong-china")
        self.assertEqual(t["components"][0]["component_id"], "U16088606")
        for definition in (ms, t):
            self.assertEqual(definition['base_currency'], 'HKD')
            coefficients = {
                row["component_id"]: row["coefficient"]
                for row in definition["components"]
            }
            self.assertEqual(coefficients["U16088571"], 0.45)
            self.assertEqual(coefficients["hang-seng-price-index"], -0.49)
            self.assertEqual(coefficients["spy-settlement"], -0.45)
            self.assertEqual(definition["constant_cash"], 58.0)
            self.assertEqual(definition["rebalancing_policy"], "fixed_units_no_daily_rebalancing")

    def test_t_currency_approval_does_not_change_ms_contract(self):
        self.assertEqual(configuration_sha256(approved_reference_definitions()['alpha-world-ms']),
            'a257272638bf2e99626cacdfcfd3977b2cfade9036b00b485446ac38a95db7fc')

    def test_definition_hash_is_stable_and_sensitive(self) -> None:
        definition = approved_reference_definitions()["alpha-world-ms"]
        first = configuration_sha256(definition)
        second = configuration_sha256(dict(reversed(list(definition.items()))))
        self.assertEqual(first, second)
        changed = dict(definition)
        changed["constant_cash"] = 57.0
        self.assertNotEqual(first, configuration_sha256(changed))

    def test_daily_runtime_is_bound_to_exact_approved_definition(self) -> None:
        definition = approved_reference_definitions()["alpha-world-ms"]
        digest = configuration_sha256(definition)
        self.assertEqual(
            definition,
            validate_reference_runtime_contract(
                "alpha-world-ms", definition, digest
            ),
        )
        changed = dict(definition)
        changed["constant_cash"] = 57.0
        with self.assertRaisesRegex(ValueError, "differs from the deployed"):
            validate_reference_runtime_contract(
                "alpha-world-ms", changed, configuration_sha256(changed)
            )
        with self.assertRaisesRegex(ValueError, "does not match its SHA-256"):
            validate_reference_runtime_contract(
                "alpha-world-ms", definition, "0" * 64
            )

    def test_studio_runtime_accepts_canonical_typed_variants_and_rejects_drift(self) -> None:
        filtered = filtered_portfolio_definition(
            subject_id="asia-ex-japan",
            display_name="Asia ex Japan",
            source_account_id="U16088606",
            base_currency="USD",
            history_start_date="2025-01-20",
            excluded_countries=("Japan",),
            excluded_exchanges=("TSE",),
            excluded_currencies=("JPY",),
            opening_cash={"USD": 100.0},
            allowed_cash_currencies=("USD",),
            include_actual_interest=False,
        )
        digest = configuration_sha256(filtered)
        self.assertEqual(
            filtered,
            validate_typed_runtime_contract(
                "asia-ex-japan", "filtered_portfolio", filtered, digest
            ),
        )
        changed = dict(filtered)
        changed["publication_policy"] = {"invent_missing_dates": True}
        with self.assertRaisesRegex(ValueError, "not canonical"):
            validate_typed_runtime_contract(
                "asia-ex-japan",
                "filtered_portfolio",
                changed,
                configuration_sha256(changed),
            )

    def test_studio_templates_cover_filtered_and_fixed_unit_portfolios(self) -> None:
        templates = derived_portfolio_studio_templates()
        self.assertEqual(
            [item["subject_type"] for item in templates],
            ["filtered_portfolio", "composite_portfolio"],
        )
        self.assertEqual(templates[0]["modelled_on"], "China & Hong Kong")
        self.assertIn("Alpha World MS", templates[1]["modelled_on"])

    def test_reference_summaries_expose_complete_owner_review_terms(self) -> None:
        definitions = approved_reference_definitions()
        hk = reference_definition_summary(definitions["hong-kong-china"])
        self.assertEqual("U16088606", hk["source"])
        self.assertEqual({"HKD": 484300.0, "USD": 0.0}, hk["opening_cash"])
        self.assertEqual(["HKD", "USD"], hk["allowed_cash_currencies"])
        self.assertIn("TWD", hk["excluded_currencies"])
        ms = reference_definition_summary(definitions["alpha-world-ms"])
        t = reference_definition_summary(definitions["alpha-world-t"])
        self.assertIn("normalized China & Hong Kong", ms["formula"])
        self.assertIn("normalized U16088606-Greater China", t["formula"])
        for summary in (ms, t):
            self.assertEqual("2025-01-20", summary["effective_from"])
            self.assertEqual(58.0, summary["constant_cash"])
            self.assertEqual(230.0, summary["initial_exposure"]["gross_risky"])
            self.assertEqual(
                "fixed_units_no_daily_rebalancing",
                summary["rebalancing_policy"],
            )

    def test_master_modes_prevent_double_counting(self) -> None:
        consolidated = master_account_definition(
            master_account_id="U15606638",
            display_name="Institution Master",
            reporting_mode="consolidated_children",
            reporting_currency="USD",
            history_start_date="2025-01-20",
            member_account_ids=("U16088571", "U16088606"),
        )
        self.assertFalse(consolidated["aggregation"]["count_master_totals"])
        self.assertTrue(consolidated["aggregation"]["count_child_totals"])
        with self.assertRaisesRegex(ValueError, "only allowed"):
            master_account_definition(
                master_account_id="U15606638",
                display_name="Institution Master",
                reporting_mode="administrative_only",
                reporting_currency="USD",
                history_start_date="2025-01-20",
                member_account_ids=("U16088571",),
            )

    def test_composite_rejects_ambiguous_benchmark_and_nonpositive_nav(self) -> None:
        with self.assertRaisesRegex(ValueError, "price convention"):
            fixed_unit_composite_definition(
                subject_id="test-composite",
                display_name="Test",
                base_currency="USD",
                normalization_date="2025-01-20",
                constant_cash=100,
                components=(
                    {
                        "component_id": "test-benchmark",
                        "component_type": "benchmark",
                        "coefficient": -0.5,
                        "source_currency": "USD",
                        "price_convention": "adjusted-something",
                    },
                ),
            )
        with self.assertRaisesRegex(ValueError, "positive NAV"):
            fixed_unit_composite_definition(
                subject_id="test-composite",
                display_name="Test",
                base_currency="USD",
                normalization_date="2025-01-20",
                constant_cash=0,
                components=(
                    {
                        "component_id": "test-account",
                        "component_type": "account",
                        "coefficient": -1,
                        "source_currency": "USD",
                    },
                ),
            )

    def test_dependency_order_matches_approved_portfolio_lineage(self) -> None:
        definitions = approved_reference_definitions()
        dependencies = []
        for definition in definitions.values():
            dependencies.extend(dependencies_for_definition(definition))
        nodes = {
            "U16088606",
            "U16088571",
            "hang-seng-price-index",
            "spy-settlement",
            *definitions,
        }
        order = topological_refresh_order(nodes, dependencies)
        self.assertLess(order.index("U16088606"), order.index("hong-kong-china"))
        self.assertLess(order.index("hong-kong-china"), order.index("alpha-world-ms"))
        self.assertLess(order.index("U16088606"), order.index("alpha-world-t"))
        self.assertLess(order.index("U16088571"), order.index("alpha-world-ms"))

    def test_dependency_cycles_fail_closed(self) -> None:
        dependencies = [
            Dependency("portfolio-a", "portfolio-b", "portfolio_component", "2025-01-20", {}),
            Dependency("portfolio-b", "portfolio-a", "portfolio_component", "2025-01-20", {}),
        ]
        with self.assertRaisesRegex(ValueError, "cycle"):
            topological_refresh_order(("portfolio-a", "portfolio-b"), dependencies)

    def test_migration_is_tenant_isolated_and_versioned(self) -> None:
        migration = Path(
            "migrations/postgres/028_self_service_subject_types.sql"
        ).read_text(encoding="utf-8")
        for table in (
            "subject_calculation_definitions",
            "master_account_memberships",
            "subject_calculation_runs",
        ):
            self.assertIn(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY", migration)
        self.assertIn("configuration_sha256", migration)
        self.assertIn("definition_version", migration)
        self.assertIn("eliminate_internal_transfers", migration)
        bootstrap = Path("scripts/bootstrap_postgres.py").read_text(encoding="utf-8")
        self.assertIn("subject_calculation_definitions", bootstrap)
        self.assertIn("master_account_memberships", bootstrap)
        self.assertIn("subject_calculation_runs", bootstrap)

    def test_self_service_catalog_exposes_all_governed_branches(self) -> None:
        catalog = onboarding_branch_catalog()
        self.assertEqual(
            [item["subject_type"] for item in catalog],
            [
                "physical_account",
                "master_account",
                "filtered_portfolio",
                "composite_portfolio",
            ],
        )
        self.assertEqual(catalog[0]["status"], "wave_0_complete")
        self.assertEqual(catalog[1]["status"], "after_composite_tests")
        self.assertEqual(catalog[2]["status"], "next_test")
        self.assertIn("double counting", catalog[1]["description"])

    def test_post_wave0_sequence_prioritizes_approved_derived_portfolios(self) -> None:
        self.assertEqual(
            POST_WAVE0_ONBOARDING_SEQUENCE,
            (
                "China & Hong Kong",
                "Alpha World MS",
                "Alpha World T",
                "Master-account test",
                "Remaining v1.1 physical accounts",
            ),
        )

    def test_reference_programme_enforces_owner_approved_sequence(self) -> None:
        programme = reference_onboarding_programme(
            subject_states={
                "U16088571": "active",
                "U16088606": "active",
            },
            discovered_account_ids=("U15606638",),
        )
        self.assertTrue(programme[0]["can_start"])
        self.assertEqual(programme[0]["subject_id"], "hong-kong-china")
        self.assertEqual(programme[1]["state"], "locked")

        after_hk = reference_onboarding_programme(
            subject_states={
                "U16088571": "active",
                "U16088606": "active",
                "hong-kong-china": "active",
            },
            discovered_account_ids=("U15606638",),
        )
        self.assertTrue(after_hk[1]["can_start"])
        self.assertFalse(after_hk[2]["can_start"])

    def test_master_stage_requires_source_discovery(self) -> None:
        states = {
            "U16088571": "active",
            "U16088606": "active",
            "hong-kong-china": "active",
            "alpha-world-ms": "active",
            "alpha-world-t": "active",
        }
        locked = reference_onboarding_programme(
            subject_states=states,
            discovered_account_ids=(),
        )
        self.assertEqual(locked[3]["state"], "locked")
        ready = reference_onboarding_programme(
            subject_states=states,
            discovered_account_ids=("U15606638",),
        )
        self.assertTrue(ready[3]["can_start"])

    def test_self_service_programme_migration_adds_typed_sources(self) -> None:
        migration = Path(
            "migrations/postgres/046_self_service_subject_programme.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("derived_definition", migration)
        self.assertIn("master_definition", migration)
        self.assertIn("calculation_contract", migration)
        self.assertIn("master_account_definitions", migration)
        self.assertIn(
            "ALTER TABLE master_account_definitions FORCE ROW LEVEL SECURITY",
            migration,
        )

    def test_derived_worker_checks_contract_before_running_calculator(self) -> None:
        worker = Path(
            "scripts/run_self_service_derived_refresh.py"
        ).read_text(encoding="utf-8")
        self.assertIn("validate_typed_runtime_contract", worker)
        self.assertLess(
            worker.index("definition = validate_typed_runtime_contract"),
            worker.index("run_id = _start_run", worker.index("def main")),
        )
        self.assertIn('"status": "matched_typed_runtime_contract"', worker)
        self.assertIn("refresh_filtered_portfolio", worker)
        self.assertIn("refresh_fixed_unit_composite", worker)


if __name__ == "__main__":
    unittest.main()
