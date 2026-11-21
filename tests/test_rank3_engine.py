import json
import unittest
from pathlib import Path

from financial_twin.engine import create_root, simulate
from financial_twin.loaders import alexia_tuition_sources, load_starter_scenario, load_synthetic_profile


CHALLENGE = Path(__file__).resolve().parents[1] / "fixtures" / "demo"


class PurchasingPowerAndLineageTests(unittest.TestCase):
    def test_cpi_real_values_and_checkpoint(self):
        scenario = {"start_date": "2025-01-01", "opening_cash_chf": 1020,
                    "annual_inflation": 0.02, "inflation_by_year": {"2026": 0},
                    "phases": [{"start_month": 0, "end_month": None,
                                "monthly_income_chf": 0, "monthly_expenses_chf": 0}]}
        root = create_root(scenario)
        self.assertEqual(root["state"]["cpi"], 100)
        self.assertEqual(root["state"]["cpi_unit"], "index points")
        self.assertEqual(root["state"]["cpi_base_date"], "2025-01-01")
        nodes = simulate(root, 13)
        self.assertAlmostEqual(nodes[11]["state"]["cpi"], 102)
        self.assertAlmostEqual(nodes[11]["state"]["real_cash_chf"], 1000)
        self.assertAlmostEqual(nodes[12]["state"]["cpi"], 102)
        self.assertEqual(nodes[12:], simulate(json.loads(json.dumps(nodes[11])), 1))
        zero = create_root({**scenario, "annual_inflation": 0, "inflation_by_year": {}})
        final = simulate(zero, 12)[-1]["state"]
        self.assertEqual(final["cash_chf"], final["real_cash_chf"])
        self.assertEqual(final["net_worth_chf"], final["real_net_worth_chf"])

    def test_loader_provenance_replay_and_changed_values(self):
        scenario = load_starter_scenario(CHALLENGE / "starter-kit/sample_profile.json",
                                         CHALLENGE / "starter-kit/scenario_config.json",
                                         "Master's degree", start_date="2025-01-01")
        root = create_root(scenario)
        record = root["scenario"]["provenance"]["one_off_costs[0].amount_chf"]
        self.assertEqual(record["value"], 2500)
        self.assertEqual(record["source"], "dataset")
        self.assertIn("scenario_config.json", record["file"])
        self.assertEqual(simulate(root, 2), simulate(create_root(json.loads(json.dumps(root["scenario"]))), 2))
        changed = simulate(root, 1, scenario_updates={"one_off_costs": [{"month": 0, "amount_chf": 8000}]})[0]
        record = changed["scenario"]["provenance"]["one_off_costs[0].amount_chf"]
        self.assertEqual((record["value"], record["source"]), (8000, "user"))
        self.assertNotIn("file", record)
        profile = create_root(load_synthetic_profile(CHALLENGE / "Data", "P00013", monthly_expenses_chf=2000))
        record = profile["scenario"]["provenance"]["debts[0].principal_chf"]
        self.assertEqual(record["row"], "L-P00013-01")
        self.assertEqual(record["as_of_date"], "2025-01-01")
        self.assertEqual(profile["scenario"]["provenance"]["assets[0].annual_return"]["source"], "assumption")
        self.assertEqual(profile["scenario"]["provenance"]["phases[0].start_month"]["source"], "assumption")

    def test_derived_payment_and_complete_metadata(self):
        scenario = {"opening_cash_chf": 1000, "annual_inflation": 0.02,
                    "phases": [{"start_month": 0, "end_month": None,
                                "monthly_income_chf": 0, "monthly_expenses_chf": 0}],
                    "debts": [{"id": "loan", "principal_chf": 1200, "term_months": 12}],
                    "provenance": {"opening_cash_chf": {"value": 1000, "source": "dataset"}}}
        root = create_root(scenario)
        records = root["scenario"]["provenance"]
        self.assertEqual(records["opening_cash_chf"]["source"], "user")
        self.assertEqual(records["annual_inflation"]["unit"], "share")
        self.assertEqual(records["debts[0].monthly_payment_chf"]["value"], 100)
        self.assertEqual(records["debts[0].monthly_payment_chf"]["source"], "assumption")
        self.assertIn("amortization", records["debts[0].monthly_payment_chf"]["transformation"])
        self.assertFalse(any(key.startswith("threshold_sources") for key in records))

    def test_three_tuition_sources_are_distinct(self):
        records = alexia_tuition_sources(CHALLENGE / "Data", CHALLENGE / "starter-kit/scenario_config.json")
        self.assertEqual([item["value"] for item in records["starter_charges"]], [2500, 2500])
        self.assertEqual(records["cohort_median"]["value"], 4800)
        self.assertEqual(records["profile_specific"]["value"], 8000)
        self.assertEqual(records["profile_specific"]["row"], "AS-P00001-02")
        self.assertEqual(records["profile_specific"]["frequency"], "annual")


if __name__ == "__main__":
    unittest.main()
