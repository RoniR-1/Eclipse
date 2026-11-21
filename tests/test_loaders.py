import unittest
import json
from pathlib import Path

from financial_twin.engine import create_root, simulate
from financial_twin.loaders import load_starter_scenario, load_synthetic_profile


CHALLENGE = Path(__file__).resolve().parents[1] / "fixtures" / "demo"


class LoaderTests(unittest.TestCase):
    def test_starter_scenario_preserves_phases_and_costs(self):
        scenario = load_starter_scenario(
            CHALLENGE / "starter-kit/sample_profile.json",
            CHALLENGE / "starter-kit/scenario_config.json",
            "Master's degree",
            start_date="2025-01-01",
        )
        self.assertEqual(scenario["opening_cash_chf"], 8000)
        self.assertEqual(scenario["phases"][1]["monthly_income_chf"], 4800)
        self.assertEqual(scenario["one_off_costs"], [
            {"month": 0, "amount_chf": 2500},
            {"month": 12, "amount_chf": 2500},
        ])

    def test_profile_uses_one_cash_snapshot_and_preserves_debt(self):
        scenario = load_synthetic_profile(
            CHALLENGE / "Data", "P00013", monthly_expenses_chf=2000
        )
        self.assertEqual(scenario["opening_cash_chf"], 8139.79)
        self.assertEqual(sum(asset["value_chf"] for asset in scenario["assets"]), 95661.46)
        self.assertEqual(scenario["debts"][0]["principal_chf"], 35407.71)
        self.assertEqual(scenario["debts"][0]["annual_interest_rate"], 0.0593)
        self.assertEqual(scenario["phases"][0]["monthly_expenses_chf"], 2000)

    def test_unknown_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            load_synthetic_profile(CHALLENGE / "Data", "unknown", monthly_expenses_chf=1)
        with self.assertRaises(ValueError):
            load_starter_scenario(
                CHALLENGE / "starter-kit/sample_profile.json",
                CHALLENGE / "starter-kit/scenario_config.json",
                "unknown",
                start_date="2025-01-01",
            )

    def test_all_starter_months_match_reference(self):
        starter = CHALLENGE / "starter-kit"
        with (starter / "sample_profile.json").open(encoding="utf-8") as source:
            profile = json.load(source)
        with (starter / "scenario_config.json").open(encoding="utf-8") as source:
            config = json.load(source)
        for original in config["scenarios"]:
            with self.subTest(scenario=original["name"]):
                scenario = load_starter_scenario(
                    starter / "sample_profile.json", starter / "scenario_config.json",
                    original["name"], start_date="2025-01-01",
                )
                cash = profile["opening_cash_chf"]
                expected = []
                for month in range(profile["horizon_months"]):
                    phase = next(item for item in original["phases"] if item["start_month"] <= month < item["end_month"])
                    cash += phase["monthly_income_chf"] - phase["monthly_expenses_chf"]
                    cash -= sum(item["amount_chf"] for item in original["one_off_costs"] if item["month"] == month)
                    expected.append(cash)
                nodes = simulate(create_root(scenario), profile["horizon_months"])
                self.assertEqual(len(nodes), len(expected))
                for node, balance in zip(nodes, expected):
                    self.assertAlmostEqual(node["state"]["cash_chf"], balance, places=2)

    def test_profile_runs_through_engine(self):
        scenario = load_synthetic_profile(
            CHALLENGE / "Data", "P00013", monthly_expenses_chf=2000
        )
        root = create_root(scenario)
        nodes = simulate(root, 12)
        self.assertEqual(root["state"]["risk_exposure"]["investment_value_chf"], 95661.46)
        self.assertEqual(len(nodes), 12)
        self.assertEqual(nodes[0]["parent_id"], root["id"])
        self.assertGreater(nodes[-1]["state"]["total_debt_chf"], 35407.71)

    def test_master_funding_need_and_terminal_cash(self):
        scenario = load_starter_scenario(CHALLENGE / "starter-kit/sample_profile.json",
                                         CHALLENGE / "starter-kit/scenario_config.json",
                                         "Master's degree", start_date="2025-01-01")
        final = simulate(create_root(scenario), 120)[-1]["state"]
        self.assertEqual(final["cash_chf"], 144600)
        self.assertEqual(final["funding_feasibility"]["maximum_funding_need_chf"], 18600)


if __name__ == "__main__":
    unittest.main()
