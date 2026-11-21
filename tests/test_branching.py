"""Life events (branches), the Test button's data, narratives, input validation and .env loading."""

from copy import deepcopy
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from financial_twin import server
from financial_twin.server import (apply_branch_change, extract, load_env, node_narrative, random_test, run_branch,
                                   run_simulation)

NO_CHANGE = {"monthly_income_delta_chf": 0, "monthly_expenses_delta_chf": 0, "one_off_cost_chf": 0,
             "investment_multiplier": 1, "new_asset_chf": 0, "new_debt_chf": 0,
             "new_debt_annual_interest_rate": 0, "new_debt_monthly_payment_chf": 0}


def timeline(years=2):
    scenario = {
        "start_date": "2026-01-01", "currency": "CHF", "opening_cash_chf": 20000, "annual_inflation": 0,
        "phases": [{"name": "Study", "start_month": 0, "end_month": 6, "monthly_income_chf": 1500, "monthly_expenses_chf": 2000},
                   {"name": "Work", "start_month": 6, "end_month": None, "monthly_income_chf": 5000, "monthly_expenses_chf": 3000}],
        "one_off_costs": [], "debts": [],
        "assets": [{"id": "etf", "type": "investment", "value_chf": 10000, "annual_return": 0, "annual_volatility": 0.1}],
        "preferences": {"savings_rate": 0, "risk_tolerance": 0, "safety_priority": 1, "emergency_months": 0,
                        "investment_annual_return": 0, "investment_annual_volatility": 0},
    }
    return run_simulation({"scenario": scenario, "years": years,
                           "variables": {"opening_cash_chf": 20000, "savings_rate": 0, "risk_tolerance": 0}})["nodes"]


def branch(parent, months=6, variables=None, **change):
    return apply_branch_change(parent, "Event", months, variables or {}, {**NO_CHANGE, **change}, 0)["nodes"]


class BranchEffectTests(unittest.TestCase):
    def setUp(self):
        self.nodes = timeline()
        self.parent = self.nodes[12]  # month 12, in the Work phase
        self.baseline = branch(self.parent)

    def test_one_off_cost_is_paid_once_in_the_branch_month(self):
        nodes = branch(self.parent, one_off_cost_chf=7000)
        self.assertEqual(nodes[0]["state"]["cash_flow"]["one_off_costs_chf"], 7000)
        self.assertEqual(nodes[1]["state"]["cash_flow"]["one_off_costs_chf"], 0)
        for with_cost, without in zip(nodes, self.baseline):
            self.assertAlmostEqual(without["state"]["cash_chf"] - with_cost["state"]["cash_chf"], 7000)

    def test_recurring_deltas_change_every_later_month(self):
        nodes = branch(self.parent, monthly_income_delta_chf=1000, monthly_expenses_delta_chf=400)
        for node in nodes:
            self.assertEqual(node["state"]["cash_flow"]["income_chf"], 6000)
            self.assertEqual(node["state"]["cash_flow"]["expenses_chf"], 3400)
        self.assertAlmostEqual(nodes[-1]["state"]["cash_chf"] - self.baseline[-1]["state"]["cash_chf"], 6 * 600)

    def test_income_and_expenses_never_go_below_zero(self):
        nodes = branch(self.parent, monthly_income_delta_chf=-99999, monthly_expenses_delta_chf=-99999)
        self.assertEqual(nodes[0]["state"]["cash_flow"]["income_chf"], 0)
        self.assertEqual(nodes[0]["state"]["cash_flow"]["expenses_chf"], 0)

    def test_financed_purchase_adds_the_asset_and_the_loan(self):
        nodes = branch(self.parent, one_off_cost_chf=20000, new_asset_chf=100000, new_debt_chf=80000,
                       new_debt_annual_interest_rate=0.024, new_debt_monthly_payment_chf=1000)
        state = nodes[0]["state"]
        self.assertTrue(any(asset["value_chf"] == 100000 for asset in state["assets"]))
        self.assertAlmostEqual(state["cash_flow"]["debt_interest_chf"], 80000 * 0.024 / 12)
        self.assertAlmostEqual(state["total_debt_chf"], 80000 + 160 - 1000)
        # Buying with cash and a loan swaps cash for an asset: net worth only falls by this month's interest.
        self.assertAlmostEqual(self.baseline[0]["state"]["net_worth_chf"] - state["net_worth_chf"], 160)

    def test_market_crash_scales_investments_at_the_branch(self):
        nodes = branch(self.parent, investment_multiplier=0.7)
        self.assertAlmostEqual(nodes[0]["state"]["risk_exposure"]["investment_value_chf"], 7000)
        self.assertAlmostEqual(self.parent["state"]["risk_exposure"]["investment_value_chf"], 10000)  # parent untouched

    def test_a_branch_keeps_the_past_and_replaces_the_planned_future_with_its_event(self):
        # Branches are one event deep: phases and one-off costs planned after the branch month are not carried
        # into the branch, so it shows the effect of this event alone.
        early = self.nodes[3]  # month 3, in the Study phase; the original plan switches to Work at month 6
        nodes = branch(early, months=6, monthly_income_delta_chf=500)
        phases = nodes[0]["scenario"]["phases"]
        self.assertEqual([(p["start_month"], p["end_month"]) for p in phases], [(0, 3), (3, None)])
        self.assertEqual([p["monthly_income_chf"] for p in phases], [1500, 2000])
        self.assertEqual(nodes[3]["state"]["cash_flow"]["income_chf"], 2000)  # month 7: the Work phase is not inherited

    def test_branch_variables_override_from_the_branch_month(self):
        nodes = branch(self.parent, variables={"monthly_income_chf": 7000, "annual_inflation": 0.12, "savings_rate": 0.3})
        self.assertEqual(nodes[0]["state"]["cash_flow"]["income_chf"], 7000)
        self.assertAlmostEqual(nodes[-1]["state"]["cash_flow"]["expenses_chf"], 3000 * 1.12 ** (5 / 12))
        self.assertEqual(nodes[0]["state"]["active_variables"]["preferences"]["savings_rate"], 0.3)

    def test_event_is_labelled_on_the_branch(self):
        nodes = apply_branch_change(self.parent, "  Buy a car  ", 3, {}, NO_CHANGE, 0)["nodes"]
        self.assertEqual(nodes[0]["event_summary"], "Buy a car")
        self.assertTrue(all(node["event_narrative"] == "Buy a car" for node in nodes))


class BranchValidationTests(unittest.TestCase):
    def setUp(self):
        self.parent = timeline(1)[6]

    def test_invalid_ai_changes_are_rejected(self):
        bad = {"negative one-off cost": {"one_off_cost_chf": -1}, "negative new debt": {"new_debt_chf": -5},
               "not a number": {"new_asset_chf": "lots"}, "infinite": {"monthly_income_delta_chf": math.inf},
               "NaN": {"investment_multiplier": math.nan}, "boolean": {"one_off_cost_chf": True}}
        for name, change in bad.items():
            with self.subTest(name), self.assertRaises(ValueError):
                apply_branch_change(self.parent, "x", 3, {}, {**NO_CHANGE, **change})
        with self.assertRaises(ValueError):
            apply_branch_change(self.parent, "x", 3, {}, {k: v for k, v in NO_CHANGE.items() if k != "new_debt_chf"})
        with self.assertRaises(ValueError):
            apply_branch_change(self.parent, "x", 3, {}, ["not", "a", "dict"])

    def test_invalid_branch_variables_are_rejected(self):
        bad = {"unknown": {"bogus": 1}, "opening cash cannot change mid-timeline": {"opening_cash_chf": 100},
               "fraction above 1": {"savings_rate": 1.5}, "negative CHF": {"monthly_income_chf": -1},
               "boolean": {"risk_tolerance": True}}
        for name, variables in bad.items():
            with self.subTest(name), self.assertRaises(ValueError):
                apply_branch_change(self.parent, "x", 3, variables, NO_CHANGE)

    def test_bad_requests_are_rejected_before_calling_the_ai(self):
        good = {"parent": self.parent, "event": "Buy a car", "months": 3}
        bad = {"no parent": {"parent": None}, "empty event": {"event": "  "}, "event too long": {"event": "x" * 4001},
               "zero months": {"months": 0}, "too many months": {"months": 601}, "boolean months": {"months": True},
               "variables not an object": {"variables": [1]}, "negative sequence": {"sequence": -1},
               "boolean sequence": {"sequence": False}}
        with patch("financial_twin.server.gemini_json") as ai:
            for name, override in bad.items():
                with self.subTest(name), self.assertRaises(ValueError):
                    run_branch({**good, **override}, "key")
            ai.assert_not_called()

    def test_parent_without_a_covering_phase_is_rejected(self):
        parent = deepcopy(self.parent)
        parent["scenario"]["phases"] = [{"name": "Old", "start_month": 0, "end_month": 2,
                                         "monthly_income_chf": 0, "monthly_expenses_chf": 0}]
        with patch("financial_twin.server.gemini_json") as ai, self.assertRaises(ValueError):
            run_branch({"parent": parent, "event": "x", "months": 1}, "key")
        ai.assert_not_called()


class TestDataTests(unittest.TestCase):
    def test_structure_of_generated_test_data(self):
        for years in (2, 5, 20):
            with self.subTest(years=years):
                data = random_test({"years": years, "seed": 11, "start_date": "2026-01-01"})
                nodes = {node["id"]: node for node in data["nodes"]}
                main = [node for node in data["nodes"] if node["branch_id"] in ("root", "main")]
                self.assertEqual(len(main), years * 12 + 1)
                branches = {node["branch_id"] for node in data["nodes"]} - {"root", "main"}
                self.assertEqual(len(branches), min(3, years - 1) + (1 if years > 2 else 0))
                for node in data["nodes"]:
                    if node["parent_id"]:
                        self.assertIn(node["parent_id"], nodes, "every node's parent exists")
                        self.assertEqual(node["month"], nodes[node["parent_id"]]["month"] + 1)
                    self.assertLessEqual(node["month"], years * 12)
                for variable in data["extraction"]["variables"]:
                    self.assertLessEqual(variable["min"], variable["value"])
                    self.assertLessEqual(variable["value"], variable["max"])
                self.assertEqual(data["nodes"][0]["date"], "2026-01-01")

    def test_invalid_test_data_requests_are_rejected(self):
        for bad in ({"years": -1}, {"years": 21}, {"seed": -1}, {"seed": "abc"}, {"seed": True}, {"start_date": "tomorrow"}):
            with self.subTest(bad), self.assertRaises(ValueError):
                random_test(bad)


class NarrativeTests(unittest.TestCase):
    def test_starting_point_and_monthly_text_use_the_calculated_numbers(self):
        nodes = timeline(1)
        self.assertEqual(node_narrative({"node": nodes[0]})["narrative"],
                         "The projection starts with CHF 20,000 in cash and CHF 30,000 in net worth.")
        text = node_narrative({"node": nodes[1]})["narrative"]
        self.assertIn("income is CHF 1,500 and expenses are CHF 2,000", text)
        self.assertIn(f"to CHF {nodes[1]['state']['cash_chf']:,.0f}", text)

    def test_invalid_nodes_are_rejected(self):
        node = timeline(1)[1]
        broken = deepcopy(node)
        broken["state"]["cash_chf"] = math.nan
        missing = deepcopy(node)
        del missing["state"]["net_worth_chf"]
        for bad in ({}, {"node": "x"}, {"node": {"state": None}}, {"node": broken}, {"node": missing}):
            with self.subTest(bad=str(bad)[:40]), self.assertRaises(ValueError):
                node_narrative(bad)


class ExtractValidationTests(unittest.TestCase):
    def test_bad_input_is_rejected_before_calling_the_ai(self):
        with patch("financial_twin.server.urlopen") as network:
            for statement, years, key in (("", 5, "k"), ("   ", 5, "k"), ("x" * 12001, 5, "k"), (None, 5, "k"),
                                          ("I earn CHF 5000.", 21, "k"), ("I earn CHF 5000.", 5, "")):
                with self.subTest(statement=str(statement)[:20], years=years, key=key), self.assertRaises(ValueError):
                    extract(statement, years, key)
            network.assert_not_called()

    def test_scenario_without_a_starting_phase_is_rejected(self):
        reply = {"summary": "s", "assumptions": "a", "opening_cash_chf": 1000, "annual_inflation": 0,
                 "phases": [{"name": "Later", "start_month": 5, "monthly_income_chf": 1, "monthly_expenses_chf": 1}],
                 "one_off_costs": [], "assets": [], "debts": [],
                 "preferences": {"savings_rate": 0, "risk_tolerance": 0, "safety_priority": 0, "emergency_months": 0,
                                 "investment_annual_return": 0, "investment_annual_volatility": 0},
                 "variable_ids": ["opening_cash_chf", "savings_rate", "risk_tolerance"]}
        with patch("financial_twin.server.gemini_json", return_value=reply), self.assertRaises(ValueError):
            extract("I earn CHF 5000.", 5, "key")


class EnvFileTests(unittest.TestCase):
    def test_env_file_is_read_without_overriding_the_real_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, ".env").write_text(
                "# comment\n\nAI_BASE_URL = 'https://example.invalid/v1'\nAI_API_KEY=\"from-file\"\nPORT=9999\nnot a setting\n",
                encoding="utf-8")
            with patch.object(server, "ROOT", Path(folder)), patch.dict(os.environ, {"PORT": "8000"}, clear=True):
                load_env()
                self.assertEqual(os.environ["AI_BASE_URL"], "https://example.invalid/v1")
                self.assertEqual(os.environ["AI_API_KEY"], "from-file")
                self.assertEqual(os.environ["PORT"], "8000")
                self.assertNotIn("not a setting", os.environ)

    def test_missing_env_file_is_fine(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, "ROOT", Path(folder)):
            load_env()


if __name__ == "__main__":
    unittest.main()
