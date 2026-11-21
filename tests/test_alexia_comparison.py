import json
import unittest
from unittest.mock import Mock

from financial_twin.comparison import alexia_comparison
from financial_twin.engine import create_root, simulate
from financial_twin.server import Handler


class AlexiaComparisonTests(unittest.TestCase):
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
