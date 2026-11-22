"""Goal scorecard: hand-checked statuses, gaps and levers, the common horizon, goal types, and AI parsing."""
import json
import unittest
from unittest.mock import patch

from financial_twin import create_root, simulate
from financial_twin.goals import extend_to_horizon, normalize_goals, scorecard
from financial_twin.server import apply_branch, parse_goals, score_goals
from financial_twin.uncertainty import analyse


def path(income=5000, expenses=3000, months=24, cash=10000, debts=()):
    scenario = {"start_date": "2026-01-01", "opening_cash_chf": cash, "annual_inflation": 0,
                "phases": [{"start_month": 0, "end_month": None, "monthly_income_chf": income, "monthly_expenses_chf": expenses}],
                "one_off_costs": [], "assets": [], "debts": list(debts)}
    root = create_root(scenario)
    return [root, *simulate(root, months)]


def cash_goal(target, when="2027-01", priority="high", name="Savings"):
    return {"name": name, "type": "cash_by_date", "target_chf": target, "date": when, "priority": priority}


def results(nodes, goals, robust=True):
    ranges = analyse(nodes)["nodes"] if robust else None
    return scorecard(nodes, goals, ranges)


def change(kind, amount):
    return {"title": "Change", "summary": "", "assumptions": "", "one_offs": [], "assets": [], "investment_multiplier": 1,
            "changes": [{"kind": kind, "label": "Change", "amount_chf": amount, "start_offset_months": 0,
                         "duration_months": 0, "source": "user"}]}


class GoalScorecardTests(unittest.TestCase):
    def test_status_gap_and_levers_match_a_hand_calculation(self):
        # 10,000 + 12 × 2,000 = 34,000 in Jan 2027; living costs ±5% move it by about ±1,800.
        goals = [cash_goal(30000, name="A"), cash_goal(33000, name="B"), cash_goal(35000, name="C"), cash_goal(40000, name="D")]
        row = results(path(), goals)["paths"][0]
        self.assertEqual([r["status"] for r in row["results"]], ["robust", "fragile", "within_reach", "missed"])
        self.assertEqual([r["gap_chf"] for r in row["results"]], [4000, 1000, -1000, -6000])
        self.assertEqual(row["results"][3]["closeness"], 0.85)
        low, high = row["results"][0]["range_chf"]
        self.assertTrue(31900 < low <= 32200 and 35800 <= high < 36100, (low, high))
        levers = {lever["kind"]: lever["text"] for lever in row["results"][3]["explain"]["levers"]}
        self.assertIn("CHF 500 more per month", levers["money"])            # 6,000 over 12 months
        self.assertIn("2027-04", levers["time"])                             # 34,000 + 3 × 2,000 = 40,000
        self.assertIn("CHF 34k", levers["target"])

    def test_short_paths_are_continued_to_the_common_horizon(self):
        nodes = path(months=36)
        short = apply_branch({"parent": nodes[6], "event": "Raise", "months": 6, "proposal": change("income", 1000)})["nodes"]
        extended, horizon = extend_to_horizon(nodes + short)
        self.assertEqual(horizon, 36)
        continued = [n for n in extended if n.get("continued")]
        self.assertEqual(len(continued), 36 - short[-1]["month"])
        card = scorecard(extended, [cash_goal(70000, when="2028-12")], None, horizon)
        rows = {row["branch_id"]: row for row in card["paths"]}
        raised = rows[short[0]["branch_id"]]
        self.assertEqual(raised["continued_from"], short[-1]["date"][:7])
        self.assertEqual(raised["tip_id"], short[-1]["id"])                  # View jumps to a node that exists
        self.assertEqual(raised["results"][0]["date"], "2028-12")             # judged at the goal date, not early
        self.assertEqual(card["paths"][0]["branch_id"], short[0]["branch_id"])

    def test_goal_after_the_horizon_is_not_scored(self):
        card = results(path(months=6), [cash_goal(1000, when="2030-06"), cash_goal(1000, when="2026-05", name="Soon")], robust=False)
        row = card["paths"][0]
        self.assertEqual(row["results"][0]["status"], "beyond_horizon")
        self.assertEqual((row["goals_scored"], row["score"]), (1, 1.0))

    def test_never_short_and_ranking_across_paths(self):
        nodes = path(income=3000, expenses=3000, cash=1000, months=12)
        branch = apply_branch({"parent": nodes[2], "event": "Bigger flat", "months": 10, "proposal": change("expenses", 500)})["nodes"]
        card = results(nodes + branch, [{"name": "Never broke", "type": "never_short", "priority": "high"}])
        rows = {row["branch_id"]: row for row in card["paths"]}
        flat = rows[branch[0]["branch_id"]]["results"][0]
        self.assertEqual(flat["status"], "missed")
        self.assertIn("Cash runs out", flat["explain"]["headline"])
        self.assertIn("living costs are", next(l["text"] for l in flat["explain"]["levers"] if l["kind"] == "cause"))
        self.assertEqual(rows["main"]["results"][0]["status"], "fragile")    # 5% higher costs would run out
        self.assertEqual(card["paths"][0]["branch_id"], "main")

    def test_debt_free_counts_a_later_loan(self):
        loan = {"id": "car", "principal_chf": 6000, "annual_interest_rate": 0, "monthly_payment_chf": 500}
        nodes = path(months=24, debts=[loan])
        card = results(nodes, [{"name": "No debt", "type": "debt_free_by_date", "date": "2027-01", "priority": "high"},
                               {"name": "Too soon", "type": "debt_free_by_date", "date": "2026-06", "priority": "high"}], robust=False)
        met, early = card["paths"][0]["results"]
        self.assertEqual(met["status"], "met")
        self.assertEqual(early["status"], "missed")
        self.assertIn("2027-01", next(l["text"] for l in early["explain"]["levers"] if l["kind"] == "time"))

    def test_monthly_surplus_and_reserve(self):
        card = results(path(months=12), [
            {"name": "Save 2.5k", "type": "monthly_surplus", "target_chf": 2500, "date": "2026-03", "priority": "high"},
            {"name": "Reserve", "type": "reserve_months", "target_months": 3, "priority": "high"}], robust=False)
        surplus, reserve = card["paths"][0]["results"]
        self.assertEqual(surplus["status"], "missed")
        self.assertEqual(surplus["gap_chf"], -500)                             # keeps 2,000 a month
        self.assertEqual(reserve["status"], "met")                             # 12,000 cash ≥ 3 × 3,000 at the low point

    def test_priority_weights_the_score(self):
        row = results(path(), [cash_goal(68000, priority="high", name="High"), cash_goal(1000, priority="low", name="Low")],
                      robust=False)["paths"][0]
        self.assertEqual(row["score"], 0.625)                                  # (3 × 0.5 + 1 × 1) / 4

    def test_invalid_goals_are_rejected(self):
        for goals in ([], [{"type": "lottery"}], [cash_goal(-5)], [cash_goal(100, when="soon")],
                      [dict(cash_goal(100), priority="urgent")], [cash_goal(100)] * 9,
                      [{"name": "R", "type": "reserve_months", "target_months": 0}]):
            with self.subTest(goals=goals), self.assertRaises(ValueError):
                normalize_goals(goals)

    def test_score_endpoint_and_ai_parsing(self):
        card = score_goals({"nodes": json.loads(json.dumps(path())), "goals": [cash_goal(30000)]})
        self.assertTrue(card["uses_range"])
        self.assertEqual(card["paths"][0]["results"][0]["status"], "robust")
        ai = {"goals": [dict(cash_goal(160000, when="2031-12", name="Flat"), target_months=0,
                             note="20% down payment plus 5% purchase costs on a CHF 640k flat")],
              "unsupported": [{"text": "be happy", "reason": "Not measurable with money and dates."}]}
        with patch("financial_twin.server.gemini_json", return_value=ai) as gemini:
            parsed = parse_goals({"text": "buy a flat in 2031 and be happy", "start_date": "2026-01-01",
                                  "monthly_costs_chf": 3000}, "")
        prompt = gemini.call_args.args[0]
        self.assertIn("buy a flat in 2031", prompt)
        self.assertIn("CHF 3,000 per month", prompt)
        self.assertEqual(parsed["goals"][0]["note"], "20% down payment plus 5% purchase costs on a CHF 640k flat")
        self.assertEqual(parsed["unsupported"][0]["text"], "be happy")
        with patch("financial_twin.server.gemini_json", return_value={"goals": [], "unsupported": []}), self.assertRaises(ValueError):
            parse_goals({"text": "something"}, "")


if __name__ == "__main__":
    unittest.main()
