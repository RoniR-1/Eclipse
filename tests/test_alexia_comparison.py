import json
from pathlib import Path
import unittest
from unittest.mock import Mock

from financial_twin.comparison import alexia_comparison
from financial_twin.engine import create_root, simulate
from financial_twin.server import Handler, alexia_demo
from financial_twin.reproduce import build_run, verify


class AlexiaComparisonTests(unittest.TestCase):
    def test_homepage_demo_numbers_and_replay(self):
        evidence = json.loads((Path(__file__).resolve().parents[1] / "fixtures/demo/homepage-evidence.json").read_text())
        demo = alexia_demo()
        scenario = demo["run"]["scenario"]
        proposal = demo["run"]["branches"][0]["proposal"]
        self.assertEqual(scenario["start_date"], evidence["start_date"])
        self.assertEqual(scenario["opening_cash_chf"], evidence["opening_cash_chf"])
        self.assertEqual(scenario["annual_inflation"], evidence["annual_inflation"])
        self.assertEqual(scenario["phases"][0]["monthly_income_chf"], evidence["work_monthly_income_chf"])
        self.assertEqual(scenario["phases"][0]["monthly_expenses_chf"], evidence["monthly_expenses_chf"])
        self.assertEqual([item["amount_chf"] for item in proposal["one_offs"]], [evidence["tuition_chf"]] * 2)
        self.assertEqual([item["offset_months"] for item in proposal["one_offs"]], evidence["tuition_months"])
        self.assertEqual(proposal["changes"][0]["duration_months"], evidence["study_months"])
        self.assertEqual(evidence["work_monthly_income_chf"] + proposal["changes"][0]["amount_chf"], evidence["study_monthly_income_chf"])
        self.assertEqual(evidence["work_monthly_income_chf"] + proposal["changes"][1]["amount_chf"], evidence["graduate_monthly_income_chf"])
        self.assertEqual(demo["years"] * 12, evidence["horizon_months"])
        baseline, alternative = demo["nodes"][60], demo["nodes"][-1]
        self.assertEqual(baseline["state"]["cash_chf"], evidence["expected_work_terminal_cash_chf"])
        self.assertEqual(alternative["state"]["cash_chf"], evidence["expected_master_terminal_cash_chf"])
        funding = alternative["state"]["funding_feasibility"]
        self.assertEqual(funding["minimum_cash_chf"], evidence["expected_master_minimum_cash_chf"])
        self.assertEqual(funding["minimum_cash_date"], evidence["expected_master_minimum_cash_date"])
        self.assertEqual(funding["first_funding_gap_period_start"], evidence["expected_first_funding_gap_period_start"])
        data = demo["run"]
        run = build_run(source=data["source"], years=data["years"], scenario=data["scenario"],
                        variables=data["variables"], branches=data["branches"], nodes=demo["nodes"])
        run["operations"] = data["operations"]
        self.assertTrue(verify(run)["ok"])

    def test_comparison_replays_starter_paths_and_sensitivity(self):
        comparison = alexia_comparison()
        self.assertEqual(comparison["start_date"], "2025-01-01")
        self.assertEqual((comparison["currency"], comparison["jurisdiction"]), ("CHF", "Switzerland"))
        self.assertEqual((comparison["profile"]["age_at_start"], comparison["profile"]["city"], comparison["profile"]["country"]), (20, "Lausanne", "CH"))
        self.assertTrue(all(check["passed"] for check in comparison["sanity_checks"]))
        master = comparison["paths"]["Master's degree"]
        metrics = master["metrics"]
        self.assertEqual(metrics["terminal_cash_chf"], 144600)
        self.assertEqual(metrics["real_terminal_cash_chf"], 144600)
        self.assertEqual(metrics["maximum_funding_need_chf"], 18600)
        self.assertEqual(metrics["first_funding_gap_month"], 7)
        self.assertEqual(metrics["first_funding_gap_period_start"], "2025-07-01")
        delta = comparison["comparisons"]["Master's degree"]
        self.assertEqual(delta["cash_difference_at_end_chf"], -7400)
        self.assertEqual(delta["largest_cash_gap_chf"], -55400)
        self.assertEqual(delta["largest_cash_gap_date"], "2027-01-01")
        self.assertIsNone(delta["break_even_date"])
        self.assertEqual(comparison["comparison_bundle"]["comparisons"]["Master's degree"], delta)
        self.assertFalse(metrics["goals"][0]["met"])
        self.assertEqual(metrics["goals"][0]["deadline_gap_chf"], 28600)
        tuition = comparison["assumptions"]["tuition_sources"]
        self.assertEqual(tuition["starter_charges"][0]["value"], 2500)
        self.assertEqual(tuition["cohort_median"]["value"], 4800)
        self.assertEqual(tuition["profile_specific"]["value"], 8000)
        for path in comparison["paths"].values():
            record = json.loads(json.dumps(path["scenario_record"]))
            self.assertEqual(record["provenance"]["goals[0].target_chf"]["source"], "assumption")
            for item in record["provenance"].values():
                if "file" in item:
                    self.assertFalse(item["file"].startswith("/"))
            final = simulate(create_root(record), comparison["horizon_months"])[-1]["state"]
            self.assertEqual(round(final["cash_chf"], 2), path["metrics"]["terminal_cash_chf"])
            self.assertEqual(final["goals"], path["metrics"]["goals"])
        driver = comparison["sensitivity"][0]
        self.assertEqual(driver["terminal_cash_impact_chf"], -750)
        self.assertEqual(driver["minimum_liquidity_impact_chf"], -750)
        self.assertEqual(driver["goal_outcomes"][0]["deadline_gap_impact_chf"], 750)
        for path_name in comparison["paths"]:
            drivers = [item for item in comparison["sensitivity"] if item["path"] == path_name]
            self.assertGreaterEqual(len(drivers), 2)
            self.assertTrue(all(item["unit"] and item["goal_outcomes"] for item in drivers))
        salary = next(item for item in comparison["sensitivity"]
                      if item["path"] == "Start working" and item["parameter"] == "monthly income, phase 1")
        self.assertEqual(salary["terminal_cash_impact_chf"], 75600)
        self.assertEqual(alexia_comparison(), comparison)

    def test_get_endpoint_returns_comparison(self):
        handler = Handler.__new__(Handler)
        handler.path = "/api/comparisons/alexia"
        handler.send_json = Mock()
        handler.do_GET()
        status, payload = handler.send_json.call_args.args
        self.assertEqual(status, 200)
        self.assertEqual(payload["paths"]["Master's degree"]["metrics"]["maximum_funding_need_chf"], 18600)
        json.dumps(payload, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
