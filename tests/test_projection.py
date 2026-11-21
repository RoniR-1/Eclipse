from collections import defaultdict
import csv
from datetime import date
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from financial_twin.projection import BASELINE, project, run_arrays, schema

ROOT = Path(__file__).resolve().parents[1]
DATA_DIRS = [ROOT / "ai-financial-twin-lauzhack-2026-main" / "Data",
             ROOT / "ai-financial-twin-lauzhack-2026" / "Data",
             ROOT.parent / "ai-financial-twin-lauzhack-2026-main" / "Data",
             ROOT / "fixtures" / "demo" / "Data"]
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
    def test_cached_provenance_and_purchase_costs(self):
        event = {"type": "housing_change", "in_months": 0,
                 "params": {"moving_cost_chf": 0},
                 "resolved_params": {"params": {"purchase_price_chf": 1000000,
                                                "down_payment_share": 0.2,
                                                "purchase_fees_share": 0.05},
                                     "source": "database_cache",
                                     "source_urls": ["https://example.org"], "note": "Estimate"}}
        result = project(full_profile(), {"Home": [event]}, horizon_years=1)
        assumptions = result["assumptions"]
        purchase = next(item for item in assumptions if item["field"] == "Purchase price")
        self.assertEqual(purchase["source"], "database_cache")
        self.assertEqual(purchase["source_urls"], ["https://example.org"])
        self.assertEqual(result["paths"]["Home"]["timeline"][1]["deposit_chf"], 200000)
        self.assertEqual(result["paths"]["Home"]["timeline"][1]["one_off_chf"], 50000)

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

    def test_fixed_term_and_detailed_housing(self):
        profile = full_profile(cash_chf=300000, debts=[{"principal_chf": 1200, "term_months": 12}])
        event = {"type": "housing_change", "in_months": 0, "params": {
            "purchase_price_chf": 1000000, "down_payment_share": 0.2, "purchase_fees_share": 0.05,
            "mortgage_interest_rate": 0, "annual_maintenance_share": 0, "moving_cost_chf": 0,
            "detailed_housing": 1, "mortgage_term_months": 100}}
        result = project(profile, {"Home": [event]}, horizon_years=1, bands=False)
        row = result["paths"]["Home"]["timeline"][1]
        self.assertEqual(row["property_asset_chf"], 1000000)
        self.assertEqual(row["mortgage_liability_chf"], 792000)
        self.assertEqual(row["deposit_chf"], 200000)
        self.assertEqual(row["deposits_chf"], 0)
        self.assertEqual(row["one_off_chf"], 50000)
        self.assertEqual(row["debt_payment_chf"], 8100)
        self.assertEqual(row["housing_purchases"][0]["fees_chf"], 50000)
        self.assertEqual(row["net_worth_chf"], 300000 - 1200 + 3500 - 50000)
        self.assertEqual(row["debt_breakdown"][1]["principal_paid_chf"], 8000)
        self.assertEqual(row["debt_service_ratio"], 8100 / 5000)

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

    def test_goal_reservations_attainment_and_deadline_reasons(self):
        goals = [{"name": "Earlier", "target_chf": 12000, "in_months": 2, "priority": "low"},
                 {"name": "Later", "target_chf": 15000, "in_months": 12, "priority": "high"},
                 {"name": "Outside", "target_chf": 1, "in_months": 600, "priority": "low"}]
        metrics = project(full_profile(goals=goals), horizon_years=1, bands=False)["paths"][BASELINE]["metrics"]
        by_name = {goal["name"]: goal for goal in metrics["goals"]}
        self.assertEqual(by_name["Later"]["reserved_cash_chf"], 15000)
        self.assertEqual(by_name["Earlier"]["prior_reserved_cash_chf"], 15000)
        self.assertFalse(by_name["Earlier"]["met"])
        self.assertEqual(by_name["Earlier"]["deadline_gap_chf"], 13000)
        self.assertEqual(by_name["Earlier"]["first_attainment_month"], 9)
        self.assertEqual(by_name["Earlier"]["failure_reason"], "insufficient_cash_at_deadline")
        self.assertIsNone(by_name["Outside"]["met"])
        self.assertIsNone(by_name["Outside"]["deadline_gap_chf"])
        self.assertEqual(by_name["Outside"]["failure_reason"], "deadline_beyond_horizon")

    def test_event_precedence_records_overlapping_changes(self):
        events = [
            {"type": "education", "in_months": 0, "params": {"duration_months": 2,
                "annual_tuition_chf": 1200, "income_share_while_studying": 0.5, "income_after_chf": 6000}},
            {"type": "relocation", "in_months": 0, "params": {"income_change_pct": 0.2,
                "rent_change_pct": 0.1, "moving_cost_chf": 0}},
            {"type": "career_change", "in_months": 1, "params": {"gap_months": 1,
                "income_share_during_gap": 0, "new_income_chf": 7000, "one_off_cost_chf": 0}},
        ]
        path = project(full_profile(age=64, retirement_age=65), {"Combined": events},
                       horizon_years=2, bands=False)["paths"]["Combined"]
        order = path["event_precedence"]
        self.assertEqual([item["type"] for item in order], ["education", "relocation", "career_change", "retirement"])
        self.assertEqual([item["input_index"] for item in order], [0, 1, 2, None])
        education = next(change for change in order[0]["changes"] if change["field"] == "monthly_income_before_bonus_chf" and change["start_month"] == 0)
        self.assertEqual((education["before_chf"], education["after_chf"], education["operation"]), (5000, 2500, "multiply"))
        relocation = next(change for change in order[1]["changes"] if change["field"] == "monthly_income_before_bonus_chf" and change["start_month"] == 0)
        self.assertEqual((relocation["before_chf"], relocation["after_chf"]), (2500, 3000))
        employment = next(change for change in order[2]["changes"] if change["field"] == "monthly_income_before_bonus_chf" and change["start_month"] == 2)
        self.assertEqual((employment["before_chf"], employment["after_chf"], employment["operation"]), (7200, 7000, "replace"))
        self.assertEqual(order[-1]["start_month"], 12)

    def test_shortfall_metrics(self):
        metrics = project(full_profile(monthly_net_income_chf=2000), horizon_years=1,
                          start="2026-01")["paths"][BASELINE]["metrics"]
        self.assertEqual(metrics["first_negative_month"], 11)   # 10,000 − 11 × 1,000 < 0
        self.assertEqual(metrics["months_in_shortfall"], 2)
        self.assertEqual(metrics["min_cash_chf"], -2000)

    def test_upfront_funding_and_deposit_reconciliation(self):
        rows = run_arrays(100, [1000], [0], one_off=[500], deposit=[0], minimum_reserve_chf=50)
        from financial_twin.projection import _metrics
        metrics = _metrics(rows, [])
        self.assertEqual(rows[1]["cash_chf"], 600)
        self.assertEqual(metrics["maximum_funding_need_chf"], 400)
        self.assertEqual(metrics["first_funding_gap_month"], 1)
        self.assertEqual(metrics["first_funding_gap_period_start"], "2025-01")
        self.assertIsNone(metrics["first_negative_month"])
        self.assertEqual(metrics["minimum_reserve_breaches"][0]["gap_chf"], 450)
        rows = run_arrays(1000, [0], [0], deposit=[123.45])
        self.assertAlmostEqual(rows[0]["cash_chf"] + rows[1]["cash_change_after_transfers_chf"], rows[1]["cash_chf"])

    def test_zero_income_debt_service_and_budget_breach(self):
        rows = run_arrays(0, [0], [0], debts=[{"principal_chf": 100, "annual_interest_rate": 0, "monthly_payment_chf": 10}])
        self.assertIsNone(rows[1]["debt_service_ratio"])
        self.assertIsNone(rows[1]["debt_service_affordable"])
        self.assertEqual(rows[1]["debt_service_affordability_status"], "undefined_zero_income")
        self.assertTrue(rows[1]["budget_breach"])

    def test_cash_change_reconciles_displayed_rounding(self):
        rows = run_arrays(0, [0.004, 0.004], [0, 0])
        self.assertEqual(rows[1]["cash_chf"], 0)
        self.assertEqual(rows[2]["cash_chf"], 0.01)
        for previous, row in zip(rows, rows[1:]):
            self.assertEqual(round(previous["cash_chf"] + row["cash_change_after_transfers_chf"], 2), row["cash_chf"])

    def test_cpi_and_calendar_override(self):
        profile = full_profile(monthly_net_income_chf=0, monthly_rent_chf=0,
                               other_fixed_costs_chf=0, discretionary_chf=0)
        unchanged = project(profile, horizon_years=1, start="2025-01", bands=False)
        row = unchanged["paths"][BASELINE]["timeline"][-1]
        self.assertEqual(row["real_cash_chf"], row["cash_chf"])
        result = project(profile, horizon_years=2, start="2025-01", bands=False,
                         annual_inflation=0.02, inflation_by_year={"2026": 0.04})
        rows = result["paths"][BASELINE]["timeline"]
        self.assertAlmostEqual(rows[12]["cpi"], 102)
        self.assertAlmostEqual(rows[12]["price_factor"], 1.02)
        self.assertAlmostEqual(rows[24]["cpi"], 100 * 1.02 * 1.04)
        self.assertAlmostEqual(rows[12]["real_cash_chf"], round(10000 / 1.02, 2))
        self.assertEqual(rows[0]["cpi_base_date"], "2025-01")
        self.assertEqual(result["inflation_coverage"]["one_offs"], "nominal fixed")

    def test_replay_frozen_scenario_and_provenance(self):
        profile = full_profile(debts=[{"principal_chf": 1200, "term_months": 12}],
                               goals=[{"name": "Goal", "target_chf": 1000, "in_months": 12}])
        event = {"type": "education", "in_months": 0,
                 "resolved_params": {"params": {"annual_tuition_chf": 8000}, "source": "database_cache",
                                     "source_urls": ["https://example.org/tuition"], "as_of_date": "2026-01-01"}}
        result = project(profile, {"Study": [event]}, horizon_years=1, start="2026-01",
                         annual_inflation=0.02)
        record = json.loads(json.dumps(result["scenario_record"]))
        tuition = record["plans"]["Study"][0]["params"]["annual_tuition_chf"]
        self.assertEqual((tuition["value"], tuition["source"], tuition["as_of_date"], tuition["frequency"]),
                         (8000, "database_cache", "2026-01-01", "annual"))
        self.assertEqual(record["debt_lineage"][0]["monthly_payment_chf"]["transformation"], "fixed-term amortization")
        replay = project(None, scenario_record=record)
        self.assertEqual(replay["paths"], result["paths"])
        self.assertEqual(replay["scenario_record"], record)
        record["profile"]["fields"]["cash_chf"]["value"] = "invalid"
        with self.assertRaises(ValueError):
            project(None, scenario_record=record)

    def test_replay_preserves_frozen_null_when_default_changes(self):
        event = {"type": "career_change", "in_months": 0, "params": {"gap_months": 0}}
        original = project(full_profile(), {"Career": [event]}, horizon_years=1, start="2025-01")
        from financial_twin.projection import EVENT_TYPES
        spec = EVENT_TYPES["career_change"]["params"]["new_income_chf"]
        with patch.dict(spec, {"default": lambda profile: 12345}):
            replay = project(None, scenario_record=original["scenario_record"])
        self.assertIsNone(replay["paths"]["Career"]["events"][0]["params"]["new_income_chf"]["value"])
        self.assertEqual(replay["paths"], original["paths"])

    def test_band_only_varies_non_user_values(self):
        fixed = project(full_profile(), horizon_years=2)["paths"][BASELINE]
        self.assertEqual(fixed["band"]["pessimistic"]["cash_chf"], [r["cash_chf"] for r in fixed["timeline"]])
        loose = project({"age": 30, "cash_chf": 0, "monthly_net_income_chf": 5000, "monthly_rent_chf": 1500},
                        horizon_years=2)["paths"][BASELINE]
        self.assertLess(loose["band"]["pessimistic"]["cash_chf"][-1], loose["timeline"][-1]["cash_chf"])
        self.assertGreater(loose["band"]["optimistic"]["cash_chf"][-1], loose["timeline"][-1]["cash_chf"])

    def test_sensitivity_tuition_goal_crossing_and_replay(self):
        profile = full_profile(cash_chf=15000, monthly_net_income_chf=0, monthly_rent_chf=0,
                               other_fixed_costs_chf=0, discretionary_chf=0,
                               goals=[{"name": "Reserve", "target_chf": 2500, "in_months": 12}])
        event = {"type": "education", "in_months": 0, "params": {"duration_months": 12,
                 "annual_tuition_chf": 12000, "income_share_while_studying": 0, "income_after_chf": 0}}
        result = project(profile, {"Study": [event]}, horizon_years=1, start="2025-01")
        path = result["paths"]["Study"]
        driver = next(item for item in path["sensitivity"] if item["field"] == "annual_tuition_chf")
        self.assertEqual((driver["base_value"], driver["perturbed_value"], driver["perturbation"]), (12000, 13800, 1800))
        self.assertEqual((driver["terminal_cash_delta_chf"], driver["minimum_liquidity_delta_chf"]), (-1800, -1800))
        self.assertEqual(driver["category"], "user_input")
        impact = driver["goal_impacts"][0]
        self.assertTrue(impact["base_met"])
        self.assertFalse(impact["perturbed_met"])
        self.assertEqual(impact["cash_at_target_delta_chf"], -1800)
        self.assertEqual(impact["deadline_gap_delta_chf"], 1300)
        zero = next(item for item in path["sensitivity"] if item["field"] == "monthly_rent_chf")
        self.assertGreater(zero["perturbed_value"], zero["base_value"])
        replay = project(None, scenario_record=result["scenario_record"])
        self.assertEqual(replay["paths"]["Study"]["sensitivity"], path["sensitivity"])
        self.assertEqual(replay["paths"]["Study"]["band"], path["band"])

    def test_sensitivity_loan_inflation_and_goal_target(self):
        profile = full_profile(cash_chf=5000, monthly_net_income_chf=3000,
                               debts=[{"name": "Loan", "principal_chf": 1200,
                                       "annual_interest_rate": 0.12, "term_months": 12}],
                               goals=[{"name": "Reserve", "target_chf": 500, "in_months": 12}])
        path = project(profile, horizon_years=1, start="2025-01", annual_inflation=0.02)["paths"][BASELINE]
        drivers = path["sensitivity"]
        rate = next(item for item in drivers if item["scope"] == "debt" and item["field"] == "annual_interest_rate")
        self.assertEqual(rate["debt_name"], "Loan")
        self.assertGreater(rate["perturbed_value"], rate["base_value"])
        self.assertLess(rate["terminal_cash_delta_chf"], 0)
        base_rate = 0.12 / 12
        new_rate = rate["perturbed_value"] / 12
        base_payment = 1200 * base_rate / (1 - (1 + base_rate) ** -12)
        new_payment = 1200 * new_rate / (1 - (1 + new_rate) ** -12)
        expected = round(round(5000 - 12 * new_payment, 2) - round(5000 - 12 * base_payment, 2), 2)
        self.assertEqual(rate["terminal_cash_delta_chf"], expected)
        self.assertFalse(any(item["scope"] == "debt" and item["field"] == "monthly_payment_chf" for item in drivers))
        goal = next(item for item in drivers if item["scope"] == "goal")
        self.assertEqual(goal["goal_name"], "Reserve")
        self.assertEqual(goal["terminal_cash_delta_chf"], 0)
        self.assertEqual(goal["goal_impacts"][0]["perturbed_target_chf"], 575)
        inflation = next(item for item in drivers if item["scope"] == "inflation" and item["inflation_year"] is None)
        self.assertEqual(inflation["terminal_cash_delta_chf"], 0)
        self.assertLess(inflation["real_terminal_cash_delta_chf"], 0)

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


class LedgerReplayTests(unittest.TestCase):
    """The recurrence must reproduce each retained monthly cash balance."""

    def test_run_arrays_matches_cash_balances(self):
        self.assertIsNotNone(DATA)
        self.assertTrue(LEDGER.is_file())
        bundled = DATA == ROOT / "fixtures" / "demo" / "Data"
        wanted = {"P00001", "P00013"} if bundled else {f"P{index:05d}" for index in range(1, 51)}
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
        self.assertEqual(set(balances), wanted)
        for profile_id, rows in balances.items():
            rows.sort(key=lambda row: row["month"])
            months = [row["month"] for row in rows]
            self.assertEqual(len(months), len(set(months)))
            self.assertEqual(len(months), 2 if bundled else 24)
            self.assertTrue(all((profile_id, month) in income or (profile_id, month) in expenses for month in months))
            timeline = run_arrays(float(rows[0]["opening_cash_chf"]),
                                  [income[(profile_id, m)] for m in months],
                                  [expenses[(profile_id, m)] for m in months])
            self.assertEqual(len(timeline), len(rows) + 1)
            for point, expected in zip(timeline[1:], rows):
                self.assertAlmostEqual(point["cash_chf"], float(expected["closing_cash_chf"]), places=1,
                                       msg=f"{profile_id} {expected['month']}")


if __name__ == "__main__":
    unittest.main()
