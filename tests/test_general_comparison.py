import unittest

from financial_twin.report import build_comparison_bundle
from financial_twin.server import run_projection


class GeneralComparisonTests(unittest.TestCase):
    def test_older_profile_has_replayable_comparison_bundle(self):
        result = run_projection({
            "start": "2026-01", "horizon_years": 5, "annual_inflation": 0.02,
            "profile": {"name": "Morgan", "age": 58, "cash_chf": 180000,
                        "monthly_net_income_chf": 9000, "monthly_rent_chf": 1800,
                        "employment_status": "employed",
                        "debts": [{"name": "Loan", "principal_chf": 12000,
                                   "annual_interest_rate": 0.04, "term_months": 24}],
                        "goals": [{"name": "Reserve", "target_chf": 30000,
                                   "in_months": 24, "priority": "high"}]},
            "scenarios": {"Purchase": [{"type": "housing_change", "start_month": 2,
                "params": {"purchase_price_chf": 500000, "down_payment_share": 0.2,
                           "purchase_fees_share": 0.03, "mortgage_interest_rate": 0.02,
                           "mortgage_term_months": 240, "detailed_housing": 1}}]}})
        bundle = result["comparison_bundle"]
        path = bundle["paths"]["Purchase"]
        self.assertEqual(bundle["scenario_record"]["profile"]["name"], "Morgan")
        self.assertEqual(path["provenance"]["profile"]["age"]["source"], "user")
        self.assertTrue(path["metrics"]["goals"])
        self.assertTrue(path["commitments"]["added"])
        self.assertTrue(path["sensitivity_summary"]["drivers"])
        self.assertTrue(bundle["limitations"])
        self.assertTrue(all(check["available"] and check["passed"] for check in path["sanity_checks"]))
        self.assertNotEqual(path["metrics"]["closing_cash_chf"], path["metrics"]["closing_real_cash_chf"])
        self.assertGreater(result["paths"]["Purchase"]["timeline"][3]["property_asset_chf"], 0)

    def test_corrupt_cash_flow_fails_independent_check(self):
        rows = [
            {"cash_chf": 100, "total_assets_chf": 100, "debt_chf": 0, "net_worth_chf": 100},
            {"cash_chf": 110, "cash_change_after_transfers_chf": 10, "income_chf": 20,
             "expenses_chf": 0, "one_off_chf": 0, "debt_payment_chf": 0, "deposit_chf": 0,
             "total_assets_chf": 110, "debt_chf": 0, "net_worth_chf": 110},
        ]
        bundle = build_comparison_bundle({"A": {"timeline": rows}}, baseline_path="A",
                    scenario_record={}, assumptions=[], comparisons={}, limitations=[])
        checks = {item["name"]: item for item in bundle["paths"]["A"]["sanity_checks"]}
        self.assertTrue(checks["cash_reconciliation"]["passed"])
        self.assertFalse(checks["cash_flow_components"]["passed"])

    def test_generic_event_defaults_and_frozen_replay(self):
        data = {"start": "2025-01", "horizon_years": 1,
                "profile": {"age": 40, "cash_chf": 10000,
                            "monthly_net_income_chf": 2000, "monthly_rent_chf": 500},
                "scenarios": {"Plan": [{"type": "education", "start_month": 0},
                                         {"type": "relocation", "start_month": 2}]}}
        result = run_projection(data)
        education, relocation = result["paths"]["Plan"]["events"]
        self.assertEqual(education["params"]["income_after_chf"]["value"], 2300)
        self.assertEqual(relocation["params"]["moving_cost_chf"]["value"], 1500)
        replay = run_projection({"scenario_record": result["scenario_record"]})
        self.assertEqual(result["paths"]["Plan"]["timeline"], replay["paths"]["Plan"]["timeline"])


if __name__ == "__main__":
    unittest.main()
