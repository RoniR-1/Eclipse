import json
import unittest

from financial_twin import create_root, simulate


class SimulationTests(unittest.TestCase):
    def scenario(self):
        return {
            "start_date": "2025-01-31",
            "opening_cash_chf": 1000,
            "phases": [{"start_month": 0, "end_month": None,
                        "monthly_income_chf": 500, "monthly_expenses_chf": 300}],
            "preferences": {"savings_rate": 1, "risk_tolerance": 1,
                            "safety_priority": 0, "investment_annual_return": 0},
        }

    def test_serialized_checkpoint_continues_identically(self):
        root = create_root(self.scenario())
        full = simulate(root, 4, branch_id="path")
        checkpoint = json.loads(json.dumps(full[1]))
        continued = simulate(checkpoint, 2, branch_id="path")
        self.assertEqual(full[2:], continued)
        self.assertEqual(full[0]["date"], "2025-02-28")
        self.assertEqual(full[1]["date"], "2025-03-31")

    def test_branch_isolation_and_absolute_event_timing(self):
        root = create_root(self.scenario())
        parent = simulate(root, 2)[-1]
        original = json.dumps(parent, sort_keys=True)
        changed = simulate(parent, 2, branch_id="education", scenario_updates={
            "phases": [{"start_month": 2, "end_month": None,
                        "monthly_income_chf": 100, "monthly_expenses_chf": 300}],
            "one_off_costs": [{"month": 2, "amount_chf": 50, "name": "Tuition"}],
        })
        baseline = simulate(parent, 2, branch_id="baseline")
        self.assertEqual(json.dumps(parent, sort_keys=True), original)
        self.assertEqual(changed[0]["parent_id"], parent["id"])
        self.assertEqual(changed[0]["state"]["cash_flow"]["one_off_costs_chf"], 50)
        self.assertEqual(changed[1]["state"]["cash_flow"]["one_off_costs_chf"], 0)
        self.assertIn("Tuition", changed[0]["event_summary"])
        self.assertNotEqual(changed[-1]["state"]["net_worth_chf"], baseline[-1]["state"]["net_worth_chf"])

    def test_transfers_debt_and_negative_return(self):
        scenario = self.scenario()
        scenario["assets"] = [{"id": "portfolio", "type": "investment", "value_chf": 1200,
                               "annual_return": -1}]
        scenario["debts"] = [{"id": "loan", "principal_chf": 1200,
                              "annual_interest_rate": 0.12, "monthly_payment_chf": 112}]
        child = simulate(create_root(scenario), 1)[0]["state"]
        self.assertAlmostEqual(child["cash_flow"]["debt_interest_chf"], 12)
        self.assertAlmostEqual(child["total_debt_chf"], 1100)
        self.assertAlmostEqual(child["cash_flow"]["investment_return_chf"], -1200)
        self.assertAlmostEqual(child["cash_flow"]["investment_contribution_chf"], 88)
        self.assertAlmostEqual(child["cash_flow"]["net_cash_flow_chf"], 0)
        self.assertAlmostEqual(child["cash_chf"], 1000)
        self.assertAlmostEqual(child["net_worth_chf"], -12)

    def test_income_and_living_costs_both_follow_inflation(self):
        scenario = self.scenario()
        scenario["annual_inflation"] = 0.12
        scenario["one_off_costs"] = [{"month": 12, "amount_chf": 100, "description": "Fee"}]
        nodes = simulate(create_root(scenario), 13)
        first, later = nodes[0]["state"]["cash_flow"], nodes[12]["state"]["cash_flow"]
        self.assertAlmostEqual(first["income_chf"], 500)
        self.assertAlmostEqual(later["income_chf"], 560)
        self.assertAlmostEqual(later["expenses_chf"], 336)
        self.assertAlmostEqual(later["income_chf"] / later["expenses_chf"], first["income_chf"] / first["expenses_chf"])
        self.assertAlmostEqual(later["one_off_costs_chf"], 100)  # dated amounts stay nominal

    def test_inflation_change_preserves_accumulated_price_level(self):
        scenario = self.scenario()
        scenario["annual_inflation"] = 0.12
        scenario["assets"] = [{"id": "fund", "type": "investment_portfolio", "value_chf": 50}]
        checkpoint = simulate(create_root(scenario), 12)[-1]
        branch = simulate(checkpoint, 2, scenario_updates={"annual_inflation": 0})
        self.assertAlmostEqual(branch[0]["state"]["cash_flow"]["expenses_chf"], 336)
        self.assertAlmostEqual(branch[1]["state"]["cash_flow"]["expenses_chf"], 336)
        self.assertAlmostEqual(branch[0]["state"]["cash_flow"]["income_chf"], 560)
        self.assertGreaterEqual(branch[0]["state"]["risk_exposure"]["investment_value_chf"], 50)
        with self.assertRaises(ValueError):
            create_root({**scenario, "annual_inflation": -1})
        with self.assertRaises(ValueError):
            create_root({**scenario, "currency": "EUR"})

    def test_missing_phase_fails_without_mutating_checkpoint(self):
        root = create_root(self.scenario())
        original = json.dumps(root, sort_keys=True)
        with self.assertRaises(ValueError):
            simulate(root, 2, scenario_updates={"phases": [{"start_month": 0, "end_month": 1,
                "monthly_income_chf": 0, "monthly_expenses_chf": 0}]})
        self.assertEqual(json.dumps(root, sort_keys=True), original)


if __name__ == "__main__":
    unittest.main()
