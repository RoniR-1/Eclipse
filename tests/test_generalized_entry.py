import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from financial_twin import create_root, simulate
from financial_twin.engine_report import engine_comparison_bundle
from financial_twin.loaders import load_synthetic_profile
from financial_twin.server import apply_branch_change, extract, run_projection, run_simulation


DATA = Path(__file__).resolve().parent.parent / "ai-financial-twin-lauzhack-2026" / "Data"


class GeneralizedEntryTests(unittest.TestCase):
    def scenario(self):
        return {"start_date": "2025-01-01", "opening_cash_chf": 2000, "annual_inflation": 0.02,
                "phases": [{"start_month": 0, "end_month": None, "monthly_income_chf": 3000, "monthly_expenses_chf": 1800}],
                "one_off_costs": [{"month": 1, "amount_chf": 3000}], "assets": [],
                "debts": [{"id": "loan", "principal_chf": 1200, "annual_interest_rate": 0.12, "monthly_payment_chf": 100}],
                "goals": [{"name": "Reserve", "target_chf": 5000, "in_months": 12, "priority": "high"}]}

    def test_simulation_report_replays_and_reconciles(self):
        result = run_simulation({"years": 1, "scenario": self.scenario(), "variables": {
            "opening_cash_chf": 2000, "monthly_income_chf": 3000, "monthly_expenses_chf": 1800}})
        bundle = result["comparison_bundle"]
        path = bundle["paths"]["Simulation"]
        self.assertTrue(all(check["passed"] for check in path["sanity_checks"]))
        self.assertEqual(path["metrics"]["maximum_funding_need_chf"], 0)
        self.assertEqual(len(path["metrics"]["goals"]), 1)
        drivers = path["sensitivity_summary"]["drivers"]
        cash = next(item for item in drivers if item["field"] == "opening_cash_chf")
        self.assertEqual(cash["terminal_cash_delta_chf"], 300)
        income = next(item for item in drivers if item["field"] == "monthly_income_chf")
        self.assertEqual(income["terminal_cash_delta_chf"], 5400)
        self.assertTrue(any(item["field"] == "annual_interest_rate" for item in drivers))

    def test_branch_report_uses_branch_checkpoint(self):
        root = create_root(self.scenario())
        parent = simulate(root, 2)[-1]
        change = {"monthly_income_delta_chf": 200, "monthly_expenses_delta_chf": 0,
                  "one_off_cost_chf": 100, "investment_multiplier": 1, "new_asset_chf": 0,
                  "new_debt_chf": 0, "new_debt_annual_interest_rate": 0, "new_debt_monthly_payment_chf": 0}
        result = apply_branch_change(parent, "New job", 3, {}, change)
        bundle = result["comparison_bundle"]
        path = bundle["paths"]["Branch"]
        self.assertTrue(all(check["passed"] for check in path["sanity_checks"]))
        self.assertTrue(path["sensitivity_summary"]["drivers"])
        self.assertEqual(bundle["scenario_record"]["checkpoint"]["month"], 2)
        self.assertEqual(path["commitments"]["fork_month"], 2)
        invalid = deepcopy([parent, *result["nodes"]])
        invalid[1]["month"] += 1
        with self.assertRaises(ValueError):
            engine_comparison_bundle(invalid)

    def test_sensitivity_preserves_prefix_after_scenario_edit(self):
        root = create_root(self.scenario())
        first = simulate(root, 2)
        phases = deepcopy(first[-1]["scenario"]["phases"])
        phases[0]["monthly_income_chf"] = 4000
        later = simulate(first[-1], 3, scenario_updates={"phases": phases})
        bundle = engine_comparison_bundle([root, *first, *later])
        path = bundle["paths"]["Simulation"]
        self.assertEqual(bundle["scenario_record"]["sensitivity_start_month"], 2)
        income = next(item for item in path["sensitivity_summary"]["drivers"] if item["field"] == "monthly_income_chf")
        self.assertEqual(income["terminal_cash_delta_chf"], 1800)

    def test_branch_report_preserves_prefork_low_and_driver_deltas(self):
        scenario = self.scenario()
        scenario["one_off_costs"] = [{"month": 0, "amount_chf": 5000}]
        root = create_root(scenario)
        prefix = simulate(root, 6)
        parent = prefix[-1]
        change = {"monthly_income_delta_chf": 200, "monthly_expenses_delta_chf": 0,
                  "one_off_cost_chf": 0, "investment_multiplier": 1, "new_asset_chf": 0,
                  "new_debt_chf": 0, "new_debt_annual_interest_rate": 0, "new_debt_monthly_payment_chf": 0}
        result = apply_branch_change(parent, "New job", 3, {}, change)
        short = result["comparison_bundle"]["paths"]["Branch"]
        full = engine_comparison_bundle([root, *prefix, *result["nodes"]])["paths"]["Branch"]
        self.assertEqual(short["metrics"]["min_cash_chf"], -3000)
        self.assertEqual(short["metrics"]["min_cash_chf"], full["metrics"]["min_cash_chf"])
        self.assertEqual(short["metrics"]["min_cash_date"], full["metrics"]["min_cash_date"])
        self.assertEqual(short["sensitivity_summary"]["drivers"], full["sensitivity_summary"]["drivers"])

    def test_csv_loader_goals_and_project_with_overrides(self):
        scenario = load_synthetic_profile(DATA, "P00013", monthly_expenses_chf=2000)
        root = create_root(scenario)
        self.assertTrue(root["scenario"]["goals"])
        self.assertEqual(root["scenario"]["provenance"]["goals[0].target_chf"]["source"], "dataset")
        result = run_projection({"profile_id": "P00013", "horizon_years": 1,
                                 "profile": {"monthly_net_income_chf": 4321},
                                 "scenarios": {"Cost": [{"type": "unexpected_expense", "start_month": 0, "params": {"amount_chf": 100}}]}})
        self.assertIn("Cost", result["paths"])
        self.assertEqual(result["profile"]["fields"]["monthly_net_income_chf"]["source"], "user")
        self.assertEqual(result["profile"]["fields"]["cash_chf"]["source"], "dataset")
        self.assertEqual(result["scenario_record"]["debt_lineage"][0]["principal_chf"]["source"], "dataset")
        self.assertEqual(result["scenario_record"]["goal_lineage"][0]["target_chf"]["source"], "dataset")

    def test_extract_preserves_goals(self):
        generated = {"summary": "Example", "assumptions": "None", "opening_cash_chf": 2000,
                     "annual_inflation": 0, "phases": [{"name": "Work", "start_month": 0,
                     "monthly_income_chf": 3000, "monthly_expenses_chf": 1800}],
                     "one_off_costs": [], "assets": [], "debts": [], "preferences": {
                     "savings_rate": 0, "risk_tolerance": 0.5, "safety_priority": 0.5,
                     "emergency_months": 3, "investment_annual_return": 0, "investment_annual_volatility": 0},
                     "goals": [{"name": "Reserve", "target_chf": 5000, "in_months": 12, "priority": "high"}],
                     "variable_ids": ["opening_cash_chf", "monthly_income_chf", "monthly_expenses_chf"]}
        with patch("financial_twin.server.gemini_json", return_value=generated):
            scenario = extract("Save 5000 in one year", 1, "test")["scenario"]
        self.assertEqual(scenario["goals"][0]["target_month"], 12)
        self.assertEqual(scenario["goals"][0]["target_chf"], 5000)


if __name__ == "__main__":
    unittest.main()
