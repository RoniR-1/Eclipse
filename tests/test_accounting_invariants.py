"""Accounting identities and boundary cases from the challenge's technical-validation checklist.

Identities are checked on every month of many seeded random timelines, branches included:
- cash identity:      C(t+1) = C(t) + net cash flow(t)
- flow breakdown:     operating flow = income - expenses - one-off costs - debt payments;
                      net cash flow = operating flow - investment contribution
- balance sheet:      net worth = total assets - total debt;  total assets = cash + other assets
- transfers:          moving cash into investments does not change net worth, so within a path
                      net worth changes by income - expenses - one-off costs - interest + investment returns
- debt:               principal is never negative; payment = principal repaid + interest
- exposure:           shortfall = max(0, -cash); investment value = sum of investment assets
- time:               each step is exactly one calendar month
"""

from datetime import date
import unittest

from financial_twin import create_root, simulate
from financial_twin.engine import is_investment_asset
from financial_twin.server import apply_branch_change, random_test

TOLERANCE = 1e-6  # CHF; floating-point rounding over long horizons
NO_CHANGE = {"monthly_income_delta_chf": 0, "monthly_expenses_delta_chf": 0, "one_off_cost_chf": 0,
             "investment_multiplier": 1, "new_asset_chf": 0, "new_debt_chf": 0,
             "new_debt_annual_interest_rate": 0, "new_debt_monthly_payment_chf": 0}


def months_between(earlier, later):
    a, b = date.fromisoformat(earlier), date.fromisoformat(later)
    return (b.year - a.year) * 12 + b.month - a.month


def scenario(**overrides):
    base = {"start_date": "2026-01-31", "currency": "CHF", "opening_cash_chf": 10000, "annual_inflation": 0,
            "phases": [{"name": "Now", "start_month": 0, "end_month": None, "monthly_income_chf": 5000, "monthly_expenses_chf": 3000}],
            "one_off_costs": [], "assets": [], "debts": [],
            "preferences": {"savings_rate": 0, "risk_tolerance": 0, "safety_priority": 1, "emergency_months": 0,
                            "investment_annual_return": 0, "investment_annual_volatility": 0}}
    base.update(overrides)
    return base


class AccountingIdentityTests(unittest.TestCase):
    def assertClose(self, first, second, message=""):
        self.assertLessEqual(abs(first - second), TOLERANCE * max(1, abs(first), abs(second)), message)

    def check_timeline(self, nodes):
        by_id = {node["id"]: node for node in nodes}
        for node in nodes:
            state, flow = node["state"], node["state"]["cash_flow"]
            label = f"{node['branch_id']} month {node['month']}"
            other_assets = sum(asset["value_chf"] for asset in state["assets"])
            self.assertClose(state["total_assets_chf"], state["cash_chf"] + other_assets, label)
            self.assertClose(state["net_worth_chf"], state["total_assets_chf"] - state["total_debt_chf"], label)
            self.assertClose(state["total_debt_chf"], sum(debt["principal_chf"] for debt in state["debts"]), label)
            self.assertTrue(all(debt["principal_chf"] >= 0 for debt in state["debts"]), label)
            self.assertClose(state["risk_exposure"]["cash_shortfall_chf"], max(0.0, -state["cash_chf"]), label)
            self.assertClose(state["risk_exposure"]["investment_value_chf"],
                             sum(a["value_chf"] for a in state["assets"] if is_investment_asset(a)), label)
            self.assertGreaterEqual(flow["investment_contribution_chf"], 0, label)
            parent = by_id.get(node["parent_id"])
            if parent is None:
                continue
            self.assertEqual(node["month"], parent["month"] + 1, label)
            self.assertEqual(months_between(parent["date"], node["date"]), 1, label)
            self.assertClose(flow["operating_cash_flow_chf"],
                             flow["income_chf"] - flow["expenses_chf"] - flow["one_off_costs_chf"] - flow["debt_payments_chf"], label)
            self.assertClose(flow["net_cash_flow_chf"], flow["operating_cash_flow_chf"] - flow["investment_contribution_chf"], label)
            self.assertClose(state["cash_chf"], parent["state"]["cash_chf"] + flow["net_cash_flow_chf"], label)
            if parent["branch_id"] == node["branch_id"] or parent["branch_id"] == "root":
                # Within a path (a branch's first month also includes the event's asset/debt changes, so it is skipped)
                change = (flow["income_chf"] - flow["expenses_chf"] - flow["one_off_costs_chf"]
                          - flow["debt_interest_chf"] + flow["investment_return_chf"])
                self.assertClose(state["net_worth_chf"] - parent["state"]["net_worth_chf"], change, label)
                principal_repaid = parent["state"]["total_debt_chf"] - state["total_debt_chf"]
                self.assertClose(flow["debt_payments_chf"], principal_repaid + flow["debt_interest_chf"], label)

    def test_identities_hold_on_seeded_random_timelines_with_branches(self):
        for seed in range(40):
            with self.subTest(seed=seed):
                data = random_test({"years": 5 + seed % 3 * 5, "seed": seed, "start_date": "2026-01-31"})
                self.check_timeline(data["nodes"])

    def test_identities_hold_with_investing_inflation_and_a_financed_purchase(self):
        root = create_root(scenario(
            annual_inflation=0.02,
            assets=[{"id": "etf", "type": "investment", "value_chf": 20000, "annual_return": 0.06, "annual_volatility": 0.15}],
            debts=[{"id": "loan", "principal_chf": 15000, "annual_interest_rate": 0.04, "monthly_payment_chf": 400}],
            preferences={"savings_rate": 0.8, "risk_tolerance": 0.9, "safety_priority": 0.2, "emergency_months": 3,
                         "investment_annual_return": 0.05, "investment_annual_volatility": 0.15}))
        main = [root, *simulate(root, 60)]
        house = apply_branch_change(main[24], "Buy a flat", 36, {}, {**NO_CHANGE, "one_off_cost_chf": 9000,
                                    "new_asset_chf": 500000, "new_debt_chf": 400000, "new_debt_annual_interest_rate": 0.02,
                                    "new_debt_monthly_payment_chf": 1500}, 0)["nodes"]
        self.assertGreater(sum(n["state"]["cash_flow"]["investment_contribution_chf"] for n in main), 0)
        self.check_timeline(main + house)


class BoundaryCaseTests(unittest.TestCase):
    def test_zero_income_drains_cash_and_flags_the_shortfall(self):
        nodes = simulate(create_root(scenario(opening_cash_chf=5000, phases=[
            {"name": "No job", "start_month": 0, "end_month": None, "monthly_income_chf": 0, "monthly_expenses_chf": 2000}])), 4)
        self.assertEqual([n["state"]["cash_chf"] for n in nodes], [3000, 1000, -1000, -3000])
        self.assertEqual(nodes[2]["state"]["risk_exposure"]["cash_shortfall_chf"], 1000)
        self.assertTrue(all(n["state"]["cash_flow"]["investment_contribution_chf"] == 0 for n in nodes))

    def test_zero_interest_loan_is_repaid_by_payments_only(self):
        nodes = simulate(create_root(scenario(debts=[
            {"id": "family", "principal_chf": 1000, "annual_interest_rate": 0, "monthly_payment_chf": 300}])), 5)
        self.assertEqual([n["state"]["total_debt_chf"] for n in nodes], [700, 400, 100, 0, 0])
        self.assertEqual([n["state"]["cash_flow"]["debt_payments_chf"] for n in nodes], [300, 300, 300, 100, 0])
        self.assertTrue(all(n["state"]["cash_flow"]["debt_interest_chf"] == 0 for n in nodes))

    def test_paid_off_loan_never_goes_negative(self):
        nodes = simulate(create_root(scenario(debts=[
            {"id": "loan", "principal_chf": 2000, "annual_interest_rate": 0.06, "monthly_payment_chf": 900}])), 6)
        principals = [n["state"]["total_debt_chf"] for n in nodes]
        self.assertEqual(principals[-1], 0)
        self.assertTrue(all(p >= 0 for p in principals))
        last_payment_month = next(i for i, p in enumerate(principals) if p == 0)
        self.assertEqual(nodes[last_payment_month + 1]["state"]["cash_flow"]["debt_payments_chf"], 0)

    def test_higher_tuition_never_leaves_more_money(self):
        def path(tuition):
            return simulate(create_root(scenario(one_off_costs=[
                {"description": "Tuition", "month": m, "amount_chf": tuition} for m in (0, 12)])), 24)
        cheap, expensive = path(730), path(4800)
        for low, high in zip(cheap, expensive):
            self.assertLessEqual(high["state"]["cash_chf"], low["state"]["cash_chf"])
            self.assertLessEqual(high["state"]["net_worth_chf"], low["state"]["net_worth_chf"])
        self.assertAlmostEqual(cheap[-1]["state"]["cash_chf"] - expensive[-1]["state"]["cash_chf"], 2 * (4800 - 730))

    def test_annual_rates_compound_to_exactly_one_year_over_twelve_months(self):
        nodes = simulate(create_root(scenario(
            annual_inflation=0.03,
            assets=[{"id": "fund", "type": "investment", "value_chf": 10000, "annual_return": 0.06, "annual_volatility": 0}])), 13)
        self.assertAlmostEqual(nodes[11]["state"]["risk_exposure"]["investment_value_chf"], 10600, places=6)
        self.assertAlmostEqual(nodes[12]["state"]["cash_flow"]["expenses_chf"], 3000 * 1.03, places=6)

    def test_negative_return_shrinks_the_portfolio_consistently(self):
        nodes = simulate(create_root(scenario(assets=[
            {"id": "fund", "type": "investment", "value_chf": 10000, "annual_return": -0.2, "annual_volatility": 0.3}])), 12)
        values = [n["state"]["risk_exposure"]["investment_value_chf"] for n in nodes]
        self.assertTrue(all(later < earlier for earlier, later in zip(values, values[1:])))
        self.assertAlmostEqual(values[-1], 8000, places=6)

    def test_unsupported_currency_and_invalid_values_are_rejected(self):
        create_root(scenario(annual_inflation=-0.02))  # deflation is allowed; only -100% or below is not
        for bad in ({"currency": "EUR"}, {"opening_cash_chf": float("nan")}, {"annual_inflation": -1},
                    {"phases": []}, {"start_date": "31.01.2026"}):
            with self.subTest(bad), self.assertRaises(ValueError):
                create_root(scenario(**bad))


if __name__ == "__main__":
    unittest.main()
