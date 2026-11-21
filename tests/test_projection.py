from collections import defaultdict
import csv
from datetime import date
import json
from pathlib import Path
import unittest

from financial_twin.projection import BASELINE, project, run_arrays, schema

ROOT = Path(__file__).resolve().parents[1]
DATA_DIRS = [ROOT / "ai-financial-twin-lauzhack-2026-main" / "Data",
             ROOT / "ai-financial-twin-lauzhack-2026" / "Data",
             ROOT.parent / "ai-financial-twin-lauzhack-2026-main" / "Data"]
DATA = next((path for path in DATA_DIRS if (path / "synthetic-cash-balances.csv").exists()), None)
LEDGER = DATA and DATA / "synthetic-transactions" / "synthetic-transactions.csv"


def full_profile(**overrides):
    """Every value user-supplied, so results do not depend on dataset defaults."""
    profile = {"age": 30, "cash_chf": 10000, "monthly_net_income_chf": 5000, "monthly_rent_chf": 1500,
               "employment_status": "employed", "household_type": "single",
               "other_fixed_costs_chf": 700, "discretionary_chf": 800, "irregular_costs_chf": 0,
               "bonus_share_of_income": 0, "investments_chf": 0, "investment_annual_return": 0}
    profile.update(overrides)
    return profile


class ProjectionTests(unittest.TestCase):
    def test_baseline_reconciles(self):
        result = project(full_profile(), horizon_years=1, start="2026-11")
        timeline = result["paths"][BASELINE]["timeline"]
        self.assertEqual(len(timeline), 13)
        self.assertEqual(timeline[0]["date"], "2026-11")
        self.assertEqual(timeline[12]["date"], "2027-11")
        self.assertEqual(timeline[12]["cash_chf"], 10000 + 12 * (5000 - 3000))
        for previous, row in zip(timeline, timeline[1:]):
            self.assertAlmostEqual(previous["cash_chf"] + row["net_cash_flow_chf"] - row["deposit_chf"],
                                   row["cash_chf"], places=2)

    def test_start_defaults_to_current_month(self):
        today = date.today()
        result = project(full_profile(), horizon_years=1)
        self.assertEqual(result["start"], f"{today.year:04d}-{today.month:02d}")

    def test_missing_values_come_from_dataset_and_are_tagged(self):
        result = project({"age": 20, "cash_chf": 8000, "monthly_net_income_chf": 1200,
                          "monthly_rent_chf": 900, "employment_status": "student"}, horizon_years=1)
        fields = result["profile"]["fields"]
        self.assertEqual(fields["monthly_rent_chf"]["source"], "user")
        self.assertEqual(fields["discretionary_chf"]["source"], "dataset")
        self.assertGreater(fields["discretionary_chf"]["value"], 0)
        self.assertEqual(fields["investments_chf"]["source"], "assumption")
        listed = {item["field"] for item in result["assumptions"]}
        self.assertIn(fields["discretionary_chf"]["label"], listed)
        self.assertNotIn(fields["monthly_rent_chf"]["label"], listed)

    def test_education_rule_and_break_even(self):
        event = {"type": "education", "in_months": 2,
                 "params": {"duration_months": 12, "annual_tuition_chf": 6000,
                            "income_share_while_studying": 0.5, "income_after_chf": 6000}}
        result = project(full_profile(), {"Degree": [event]}, horizon_years=5, start="2026-01")
        rows = result["paths"]["Degree"]["timeline"]
        self.assertEqual(rows[3]["income_chf"], 2500)
        self.assertEqual(rows[3]["expenses_chf"], 3500)
        self.assertEqual(rows[15]["income_chf"], 6000)
        self.assertEqual(rows[14]["events"], ["Graduation"])
        comparison = result["comparisons"]["Degree"]
        # Studying costs 3,000/month for 12 months (−36,000); afterwards +1,000/month → 36 months to recover.
        self.assertEqual(comparison["largest_cash_gap_chf"], -36000)
        self.assertEqual(comparison["break_even_month"], 14 + 36)
        self.assertIsNone(project(full_profile(), {"Degree": [event]}, horizon_years=4,
                                  start="2026-01")["comparisons"]["Degree"]["break_even_month"])

    def test_housing_deposit_hits_liquidity_not_net_worth(self):
        event = {"type": "housing_change", "date": "2026-03",
                 "params": {"new_rent_chf": 2000, "deposit_months": 3, "moving_cost_chf": 0}}
        result = project(full_profile(), {"Move": [event]}, horizon_years=1, start="2026-01")
        base = result["paths"][BASELINE]["timeline"]
        move = result["paths"]["Move"]["timeline"]
        self.assertEqual(move[3]["deposits_chf"], 6000)
        self.assertEqual(move[3]["cash_chf"], base[3]["cash_chf"] - 6000 - 500)
        self.assertEqual(move[3]["net_worth_chf"], base[3]["net_worth_chf"] - 500)

    def test_career_change_with_new_income(self):
        event = {"type": "career_change", "in_months": 0,
                 "params": {"gap_months": 2, "new_income_chf": 7000}}
        rows = project(full_profile(), {"Job": [event]}, horizon_years=1)["paths"]["Job"]["timeline"]
        self.assertEqual([row["income_chf"] for row in rows[1:4]], [0, 0, 7000])

    def test_retirement_switches_income_in_every_path(self):
        result = project(full_profile(age=64, pension_share_of_income=0.5), {"Shock": [
            {"type": "unexpected_expense", "in_months": 1, "params": {"amount_chf": 1000}}]},
            horizon_years=2, start="2026-01")
        for path in result["paths"].values():
            rows = path["timeline"]
            self.assertEqual(rows[12]["income_chf"], 5000)
            self.assertEqual(rows[13]["income_chf"], 2500)
            self.assertIn("Retirement at 65", rows[12]["events"])

    def test_debt_interest_and_payment(self):
        result = project(full_profile(debts=[{"principal_chf": 12000, "annual_interest_rate": 0.12,
                                              "monthly_payment_chf": 520}]), horizon_years=1)
        row = result["paths"][BASELINE]["timeline"][1]
        self.assertAlmostEqual(row["debt_interest_chf"], 120)
        self.assertEqual(row["debt_payment_chf"], 520)
        self.assertEqual(row["debt_chf"], 11600)
        self.assertEqual(row["cash_chf"], 10000 + 2000 - 520)

    def test_goals_share_cash_in_priority_order(self):
        goals = [{"name": "Car", "target_chf": 20000, "in_months": 12, "priority": "medium"},
                 {"name": "Reserve", "target_chf": 15000, "in_months": 12, "priority": "high"},
                 {"name": "Late", "target_chf": 1, "in_months": 600}]
        metrics = project(full_profile(goals=goals), horizon_years=1)["paths"][BASELINE]["metrics"]
        by_name = {goal["name"]: goal for goal in metrics["goals"]}
        self.assertTrue(by_name["Reserve"]["met"])         # 34,000 available at month 12
        self.assertFalse(by_name["Car"]["met"])            # only 19,000 left after the reserve
        self.assertIsNone(by_name["Late"]["met"])
        self.assertEqual((metrics["goals_met"], metrics["goals_evaluated"]), (1, 2))

    def test_shortfall_metrics(self):
        metrics = project(full_profile(monthly_net_income_chf=2000), horizon_years=1,
                          start="2026-01")["paths"][BASELINE]["metrics"]
        self.assertEqual(metrics["first_negative_month"], 11)   # 10,000 − 11 × 1,000 < 0
        self.assertEqual(metrics["months_in_shortfall"], 2)
        self.assertEqual(metrics["min_cash_chf"], -2000)

    def test_band_only_varies_non_user_values(self):
        fixed = project(full_profile(), horizon_years=2)["paths"][BASELINE]
        self.assertEqual(fixed["band"]["pessimistic"]["cash_chf"], [r["cash_chf"] for r in fixed["timeline"]])
        loose = project({"age": 30, "cash_chf": 0, "monthly_net_income_chf": 5000, "monthly_rent_chf": 1500},
                        horizon_years=2)["paths"][BASELINE]
        self.assertLess(loose["band"]["pessimistic"]["cash_chf"][-1], loose["timeline"][-1]["cash_chf"])
        self.assertGreater(loose["band"]["optimistic"]["cash_chf"][-1], loose["timeline"][-1]["cash_chf"])

    def test_invalid_inputs_are_rejected(self):
        bad = [
            ({"cash_chf": 1}, None),
            (full_profile(salary=1), None),
            (full_profile(age=True), None),
            (full_profile(), {"X": [{"type": "lottery"}]}),
            (full_profile(), {"X": [{"type": "relocation", "params": {"rent_change_pct": 9}}]}),
            (full_profile(), {"X": [{"type": "relocation", "date": "2020-01"}]}),
            (full_profile(), {BASELINE: []}),
        ]
        for profile, scenarios in bad:
            with self.subTest(profile=profile, scenarios=scenarios), self.assertRaises(ValueError):
                project(profile, scenarios, start="2026-01")

    def test_output_and_schema_are_json(self):
        result = project(full_profile(), {"Move": [{"type": "relocation", "in_months": 3}]}, horizon_years=1)
        json.dumps(result, allow_nan=False)
        described = json.loads(json.dumps(schema(), allow_nan=False))
        self.assertEqual(described["profile"]["monthly_rent_chf"]["source"], "user")
        self.assertEqual(described["events"]["housing_change"]["params"]["deposit_months"]["min"], 0)


@unittest.skipUnless(DATA and LEDGER.exists(), "challenge data not available")
class LedgerReplayTests(unittest.TestCase):
    """The recurrence must reproduce the organisers' monthly cash reconciliation exactly."""

    def test_run_arrays_matches_cash_balances(self):
        wanted = {f"P{index:05d}" for index in range(1, 51)}
        income, expenses = defaultdict(float), defaultdict(float)
        with LEDGER.open(newline="", encoding="utf-8") as source:
            for row in csv.DictReader(source):
                if row["profile_id"] in wanted:
                    amount = float(row["amount_chf"])
                    key = (row["profile_id"], row["date"][:7])
                    if amount >= 0:
                        income[key] += amount
                    else:
                        expenses[key] -= amount
        balances = defaultdict(list)
        with (DATA / "synthetic-cash-balances.csv").open(newline="", encoding="utf-8") as source:
            for row in csv.DictReader(source):
                if row["profile_id"] in wanted:
                    balances[row["profile_id"]].append(row)
        for profile_id, rows in balances.items():
            months = [row["month"] for row in rows]
            timeline = run_arrays(float(rows[0]["opening_cash_chf"]),
                                  [income[(profile_id, m)] for m in months],
                                  [expenses[(profile_id, m)] for m in months])
            for point, expected in zip(timeline[1:], rows):
                self.assertAlmostEqual(point["cash_chf"], float(expected["closing_cash_chf"]), places=1,
                                       msg=f"{profile_id} {expected['month']}")


if __name__ == "__main__":
    unittest.main()
