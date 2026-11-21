"""Uncertainty ranges: re-simulation fidelity, hand-checked driver effects, widening over time, validation."""
import json
import unittest

from financial_twin import create_root, simulate
from financial_twin.server import apply_branch, random_test
from financial_twin.uncertainty import GROUPS, _ordered, _scaled, _shift, analyse, resimulate


def path(income=5000, expenses=3000, months=12, inflation=0.0, assets=(), cash=10000):
    scenario = {"start_date": "2026-01-01", "opening_cash_chf": cash, "annual_inflation": inflation,
                "phases": [{"start_month": 0, "end_month": None, "monthly_income_chf": income,
                            "monthly_expenses_chf": expenses}],
                "one_off_costs": [], "assets": list(assets), "debts": []}
    root = create_root(scenario)
    return [root, *simulate(root, months)]


def driver(result, branch, group):
    return next(item for item in result["drivers"][branch] if item["group"] == group)


class UncertaintyTests(unittest.TestCase):
    def test_unadjusted_resimulation_reproduces_every_node_on_screen(self):
        for seed in (1, 7, 42):
            nodes = json.loads(json.dumps(random_test({"years": 5, "seed": seed, "start_date": "2026-01-01"})["nodes"]))
            old, order = _ordered(nodes)
            again = resimulate(old, order, _scaled(_shift(), 0.15, 1))
            for node in nodes:
                for field in ("cash_chf", "net_worth_chf"):
                    with self.subTest(seed=seed, node=node["id"], field=field):
                        self.assertAlmostEqual(again[node["id"]]["state"][field], node["state"][field], places=6)

    def test_living_costs_driver_matches_a_hand_calculation(self):
        result = analyse(path(months=12))
        costs = driver(result, "main", "living_costs")
        # ±5% of CHF 3,000 living costs for 12 months
        self.assertAlmostEqual(costs["low"], -1800, places=6)
        self.assertAlmostEqual(costs["high"], 1800, places=6)
        self.assertAlmostEqual(driver(result, "main", "event_effects")["high"], 0, places=6)   # no events
        self.assertNotIn("income", [item["group"] for item in result["drivers"]["main"]])   # held fixed

    def test_event_effects_scale_with_the_size_of_the_change(self):
        nodes = path(months=6)
        raise_ = {"title": "Raise", "summary": "", "assumptions": "", "one_offs": [], "assets": [], "investment_multiplier": 1,
                  "changes": [{"kind": "income", "label": "Raise", "amount_chf": 1000, "start_offset_months": 0,
                               "duration_months": 0, "source": "ai_estimate"}]}
        branch = apply_branch({"parent": nodes[2], "event": "Raise", "months": 10, "proposal": raise_})["nodes"]
        result = analyse(nodes + branch)
        effect = driver(result, branch[0]["branch_id"], "event_effects")
        # ±15% of the CHF 1,000 raise for the 10 branch months
        self.assertAlmostEqual(effect["low"], -1500, places=6)
        self.assertAlmostEqual(effect["high"], 1500, places=6)

    def test_range_contains_the_shown_value_and_widens_over_time(self):
        nodes = path(months=36, inflation=0.02,
                     assets=[{"id": "etf", "type": "investment", "value_chf": 20000, "annual_return": 0.05, "annual_volatility": 0.15}])
        result = analyse(nodes)
        widths = []
        for node in nodes:
            low, high = result["nodes"][node["id"]]["net_worth"]
            self.assertLessEqual(low, node["state"]["net_worth_chf"])
            self.assertGreaterEqual(high, node["state"]["net_worth_chf"])
            widths.append(high - low)
        self.assertEqual(widths[0], 0)
        self.assertTrue(all(later >= earlier - 1e-6 for earlier, later in zip(widths, widths[1:])))
        self.assertGreater(widths[-1], widths[12])

    def test_pessimistic_shortfall_is_reported_per_branch(self):
        nodes = path(income=3000, expenses=3000, months=24, cash=1000)
        result = analyse(nodes)
        self.assertTrue(all(node["state"]["cash_chf"] >= 0 for node in nodes))   # never short in the central case
        self.assertEqual(result["first_shortfall"]["main"]["month"], 7)           # 1,000 − 7 × 150 < 0
        self.assertEqual(set(GROUPS) - {"income"}, {item["group"] for item in result["drivers"]["main"]})

    def test_invalid_input_is_rejected(self):
        for nodes, uncertainty in (([], 0.15), ([{"id": "x"}], 0.15), (path(months=1), 0.9), (path(months=1), True)):
            with self.subTest(uncertainty=uncertainty), self.assertRaises(ValueError):
                analyse(nodes, uncertainty)
        orphan = path(months=2)
        orphan[2] = {**orphan[2], "parent_id": "missing"}
        with self.assertRaises(ValueError):
            analyse(orphan)


if __name__ == "__main__":
    unittest.main()
