"""Equal-horizon path comparison: hand-checked differences, break-even, purchases, shortfalls, continuation."""
import json
import unittest

from financial_twin import create_root, simulate
from financial_twin.goals import extend_to_horizon
from financial_twin.path_compare import compare_paths
from financial_twin.server import apply_branch, compare_all_paths


def path(income=5000, expenses=3000, months=24, cash=10000):
    scenario = {"start_date": "2026-01-01", "opening_cash_chf": cash, "annual_inflation": 0,
                "phases": [{"start_month": 0, "end_month": None, "monthly_income_chf": income, "monthly_expenses_chf": expenses}],
                "one_off_costs": [], "assets": [], "debts": []}
    root = create_root(scenario)
    return [root, *simulate(root, months)]


def plan(changes=(), one_offs=(), assets=()):
    return {"title": "Event", "summary": "", "assumptions": "", "investment_multiplier": 1,
            "changes": list(changes), "one_offs": list(one_offs), "assets": list(assets)}


def change(kind, amount, offset=0, duration=0):
    return {"kind": kind, "label": "Change", "amount_chf": amount, "start_offset_months": offset,
            "duration_months": duration, "source": "user"}


def branch(parent, proposal, months):
    return apply_branch({"parent": parent, "event": "Event", "months": months, "proposal": proposal})["nodes"]


def rows(nodes):
    extended, _ = extend_to_horizon(nodes)
    return {row["branch_id"]: row for row in compare_paths(extended)["paths"]}


class PathCompareTests(unittest.TestCase):
    def test_raise_is_explained_by_income(self):
        main = path()
        raised = branch(main[6], plan([change("income", 800)]), 18)
        row = rows(main + raised)[raised[0]["branch_id"]]
        self.assertEqual(row["net_worth_vs_original_chf"], 18 * 800)
        self.assertEqual(row["break_even"]["status"], "always")
        self.assertIn("income (+CHF 14.4k)", " ".join(row["explanation"]))

    def test_study_break_falls_behind_then_overtakes(self):
        main = path()
        study = branch(main[2], plan([change("income", -2000, 0, 6), change("income", 1500, 6)]), 22)
        row = rows(main + study)[study[0]["branch_id"]]
        # −12,000 during six months of study, then +1,500 a month: level after 8 more months.
        self.assertEqual(row["break_even"]["status"], "overtakes")
        self.assertEqual(row["break_even"]["date"], "2027-05")
        self.assertIn("overtakes the original path in 2027-05", " ".join(row["explanation"]))

    def test_a_purchase_is_not_counted_as_a_loss(self):
        main = path()
        car = {"label": "Car", "kind": "vehicle", "price_chf": 20000, "loan_chf": 0, "down_payment_chf": 20000,
               "transaction_costs_chf": 0, "annual_interest_rate": 0, "monthly_payment_chf": 0,
               "annual_value_change": 0, "source": "user"}
        bought = branch(main[3], plan(assets=[car]), 21)
        row = rows(main + bought)[bought[0]["branch_id"]]
        self.assertAlmostEqual(row["net_worth_vs_original_chf"], 0, places=6)
        self.assertEqual(row["cash_vs_original_chf"], -20000)
        text = " ".join(row["explanation"])
        self.assertIn("Purchases moved CHF 20k of cash into things you own", text)
        self.assertNotIn("Working against", text)

    def test_shortfall_and_continuation(self):
        main = path(income=3000, expenses=3000, cash=1000, months=24)
        rent = branch(main[2], plan([change("expenses", 500)]), 6)
        result = rows(main + rent)
        row = result[rent[0]["branch_id"]]
        self.assertEqual(row["continued_from"], rent[-1]["date"][:7])
        self.assertEqual(row["first_shortfall"], "2026-06")          # 1,000 − 3 × 500 < 0 in the third branch month
        self.assertEqual(row["months_short"], 24 - 5 + 1)
        self.assertIn("funding gap", " ".join(row["explanation"]))
        self.assertIsNone(result["main"]["first_shortfall"])

    def test_endpoint_adds_ranges_and_orders_original_first(self):
        main = path()
        raised = branch(main[6], plan([change("income", 800)]), 18)
        out = compare_all_paths({"nodes": json.loads(json.dumps(main + raised))})
        self.assertTrue(out["paths"][0]["is_original"])
        self.assertIn("net_worth_range_chf", out["paths"][1])
        self.assertEqual(out["horizon"], "2028-01")


if __name__ == "__main__":
    unittest.main()
