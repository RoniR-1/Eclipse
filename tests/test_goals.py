"""Goal scorecard: hand-checked statuses and gaps, ranking, validation, and the AI parsing step."""
import json
import unittest
from unittest.mock import patch

from financial_twin import create_root, simulate
from financial_twin.goals import normalize_goals, scorecard
from financial_twin.server import apply_branch, parse_goals, score_goals
from financial_twin.uncertainty import analyse


def path(income=5000, expenses=3000, months=24, cash=10000):
    scenario = {"start_date": "2026-01-01", "opening_cash_chf": cash, "annual_inflation": 0,
                "phases": [{"start_month": 0, "end_month": None, "monthly_income_chf": income, "monthly_expenses_chf": expenses}],
                "one_off_costs": [], "assets": [], "debts": []}
    root = create_root(scenario)
    return [root, *simulate(root, months)]


def cash_goal(target, when="2027-01", priority="high", name="Savings"):
    return {"name": name, "type": "cash_by_date", "target_chf": target, "date": when, "priority": priority}


def results(nodes, goals, robust=True):
    ranges = analyse(nodes)["nodes"] if robust else None
    return scorecard(nodes, goals, ranges)


class GoalScorecardTests(unittest.TestCase):
    def test_status_and_gap_match_a_hand_calculation(self):
        # 10,000 + 12 × 2,000 = 34,000 in Jan 2027; living costs ±5% move it by ±1,800.
        nodes = path()
        goals = [cash_goal(30000, name="A"), cash_goal(33000, name="B"), cash_goal(35000, name="C"), cash_goal(40000, name="D")]
        row = results(nodes, goals)["paths"][0]
        statuses = [r["status"] for r in row["results"]]
        self.assertEqual(statuses, ["robust", "fragile", "within_reach", "missed"])
        self.assertEqual([r["gap_chf"] for r in row["results"]], [4000, 1000, -1000, -6000])
        self.assertEqual(row["results"][3]["closeness"], 0.85)
        # ±1,800 from living costs, plus a little from inflation ±1 point on the indexed income and costs
        low, high = row["results"][0]["range_chf"]
        self.assertTrue(31900 < low <= 32200 and 35800 <= high < 36100, (low, high))

    def test_never_short_and_ranking_across_paths(self):
        nodes = path(income=3000, expenses=3000, cash=1000, months=12)
        rent = {"title": "Bigger flat", "summary": "", "assumptions": "", "one_offs": [], "assets": [], "investment_multiplier": 1,
                "changes": [{"kind": "expenses", "label": "Rent", "amount_chf": 500, "start_offset_months": 0,
                             "duration_months": 0, "source": "user"}]}
        branch = apply_branch({"parent": nodes[2], "event": "Bigger flat", "months": 10, "proposal": rent})["nodes"]
        goals = [{"name": "Never broke", "type": "never_short", "target_chf": 0, "date": "", "priority": "high"}]
        card = results(nodes + branch, goals)
        by_branch = {row["branch_id"]: row for row in card["paths"]}
        flat = by_branch[branch[0]["branch_id"]]["results"][0]
        self.assertEqual(flat["status"], "missed")
        self.assertIn("first negative", flat["note"])
        self.assertEqual(by_branch["main"]["results"][0]["status"], "fragile")   # 5% higher costs would run out
        self.assertEqual(card["paths"][0]["branch_id"], "main")

    def test_priority_weights_the_score(self):
        nodes = path()
        goals = [cash_goal(68000, priority="high", name="High"), cash_goal(1000, priority="low", name="Low")]
        row = results(nodes, goals, robust=False)["paths"][0]
        # closeness 0.5 (high, weight 3) and 1.0 (low, weight 1): (3 × 0.5 + 1 × 1) / 4
        self.assertEqual(row["score"], 0.625)
        self.assertFalse(results(nodes, goals, robust=False)["uses_range"])

    def test_goal_after_the_horizon_is_judged_at_the_last_month(self):
        row = results(path(months=6), [cash_goal(1000, when="2030-06")], robust=False)["paths"][0]["results"][0]
        self.assertIn("after this path ends", row["note"])
        self.assertEqual(row["date"], "2026-07")

    def test_invalid_goals_are_rejected(self):
        for goals in ([], [{"type": "lottery"}], [cash_goal(-5)], [cash_goal(100, when="soon")],
                      [dict(cash_goal(100), priority="urgent")], [cash_goal(100)] * 9):
            with self.subTest(goals=goals), self.assertRaises(ValueError):
                normalize_goals(goals)

    def test_score_endpoint_and_ai_parsing(self):
        nodes = json.loads(json.dumps(path()))
        card = score_goals({"nodes": nodes, "goals": [cash_goal(30000)]})
        self.assertTrue(card["uses_range"])
        self.assertEqual(card["paths"][0]["results"][0]["status"], "robust")
        ai = {"goals": [cash_goal(20000, when="2028-12"),
                        {"name": "Never broke", "type": "never_short", "target_chf": 0, "date": "", "priority": "medium"}]}
        with patch("financial_twin.server.gemini_json", return_value=ai) as gemini:
            parsed = parse_goals({"text": "20k savings by 2028 and never run out of cash", "start_date": "2026-01-01"}, "")
        self.assertIn("20k savings by 2028", gemini.call_args.args[0])
        self.assertEqual([g["type"] for g in parsed["goals"]], ["cash_by_date", "never_short"])
        with patch("financial_twin.server.gemini_json", return_value={"goals": []}), self.assertRaises(ValueError):
            parse_goals({"text": "be happy"}, "")


if __name__ == "__main__":
    unittest.main()
