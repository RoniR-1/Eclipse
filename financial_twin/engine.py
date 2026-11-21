from __future__ import annotations

from calendar import monthrange
from copy import deepcopy
from datetime import date
from functools import reduce
from hashlib import sha256
import json
import math
from operator import add
import pickle
import numpy as np


def _number(value, name, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    value = float(value)
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise ValueError(f"{name} is out of range")
    return value


def _month(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _preferences(values):
    result = {"savings_rate": 0.0, "risk_tolerance": 0.5, "safety_priority": 0.5,
              "emergency_months": 3.0, "investment_annual_return": 0.0,
              "investment_annual_volatility": 0.0}
    unknown = set(values) - set(result)
    if unknown:
        raise ValueError(f"Unknown preferences: {sorted(unknown)}")
    result.update(values)
    for key in ("savings_rate", "risk_tolerance", "safety_priority"):
        result[key] = _number(result[key], key, 0, 1)
    result["emergency_months"] = _number(result["emergency_months"], "emergency_months", 0)
    result["investment_annual_return"] = _number(result["investment_annual_return"], "investment_annual_return", -1)
    result["investment_annual_volatility"] = _number(result["investment_annual_volatility"], "investment_annual_volatility", 0)
    return result


def _scenario(value):
    scenario = deepcopy(value)
    if not isinstance(scenario, dict):
        raise ValueError("scenario must be an object")
    try:
        date.fromisoformat(scenario.get("start_date", "2025-01-01"))
    except (TypeError, ValueError) as exc:
        raise ValueError("start_date must be an ISO date") from exc
    scenario.setdefault("start_date", "2025-01-01")
    scenario.setdefault("currency", "CHF")
    if scenario["currency"] != "CHF":
        raise ValueError("currency must be CHF")
    scenario["opening_cash_chf"] = _number(scenario.get("opening_cash_chf", 0), "opening_cash_chf")
    scenario["annual_inflation"] = _number(scenario.get("annual_inflation", 0), "annual_inflation", -1)
    if scenario["annual_inflation"] == -1:
        raise ValueError("annual_inflation must be greater than -1")
    overrides = scenario.setdefault("inflation_by_year", {})
    if not isinstance(overrides, dict):
        raise ValueError("inflation_by_year must be an object")
    for year, rate in overrides.items():
        if not str(year).isdigit() or len(str(year)) != 4:
            raise ValueError("inflation year must be a calendar year")
        overrides[year] = _number(rate, "year inflation", -0.999999)
    terms = scenario.setdefault("expense_terms", [])
    if not isinstance(terms, list):
        raise ValueError("expense_terms must be a list")
    for term in terms:
        _month(term["start_month"], "expense start")
        if term.get("end_month") is not None:
            _month(term["end_month"], "expense end")
            if term["end_month"] <= term["start_month"]:
                raise ValueError("expense duration must be positive")
        term["monthly_expenses_chf"] = _number(term["monthly_expenses_chf"], "expense", 0)
        term["phase_expenses_chf"] = _number(term["phase_expenses_chf"], "phase expense", 0)
        if not isinstance(term.get("inflation_exempt"), bool):
            raise ValueError("inflation_exempt must be a boolean")
    scenario["preferences"] = _preferences(scenario.get("preferences", {}))
    phases = scenario.get("phases", [])
    if not isinstance(phases, list) or not phases:
        raise ValueError("phases must be a non-empty list")
    intervals = []
    for phase in phases:
        start = _month(phase["start_month"], "start_month")
        end = phase["end_month"]
        if end is not None:
            end = _month(end, "end_month")
        if end is not None and end <= start:
            raise ValueError("phase end_month must be greater than start_month")
        phase["monthly_income_chf"] = _number(phase["monthly_income_chf"], "monthly_income_chf")
        phase["monthly_expenses_chf"] = _number(phase["monthly_expenses_chf"], "monthly_expenses_chf", 0)
        intervals.append((start, end if end is not None else float("inf")))
    intervals.sort()
    if any(left[1] > right[0] for left, right in zip(intervals, intervals[1:])):
        raise ValueError("phases must not overlap")
    for cost in scenario.setdefault("one_off_costs", []):
        _month(cost["month"], "one-off month")
        cost["amount_chf"] = _number(cost["amount_chf"], "one-off amount_chf", 0)
    ids = set()
    for asset in scenario.setdefault("assets", []):
        asset_id = asset["id"]
        if not isinstance(asset_id, str) or not asset_id or asset_id in ids or asset_id == "__contributions__":
            raise ValueError("asset ids must be unique non-empty strings")
        ids.add(asset_id)
        asset["value_chf"] = _number(asset["value_chf"], "asset value_chf", 0)
        asset["annual_return"] = _number(asset.get("annual_return", 0), "asset annual_return", -1)
        asset["annual_volatility"] = _number(asset.get("annual_volatility", 0), "asset annual_volatility", 0)
    ids = set()
    for debt in scenario.setdefault("debts", []):
        debt_id = debt["id"]
        if not isinstance(debt_id, str) or not debt_id or debt_id in ids:
            raise ValueError("debt ids must be unique non-empty strings")
        ids.add(debt_id)
        debt["principal_chf"] = _number(debt["principal_chf"], "debt principal_chf", 0)
        debt["annual_interest_rate"] = _number(debt.get("annual_interest_rate", 0), "annual_interest_rate", 0)
        debt["monthly_payment_chf"] = _number(debt.get("monthly_payment_chf", 0), "monthly_payment_chf", 0)
    return scenario


def _date_at(start, months):
    initial = date.fromisoformat(start)
    year, month = divmod(initial.year * 12 + initial.month - 1 + months, 12)
    return date(year, month + 1, min(initial.day, monthrange(year, month + 1)[1])).isoformat()


def _id(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(payload.encode()).hexdigest()[:24]


def is_investment_asset(asset):
    asset_type = str(asset.get("type", "")).lower().replace(" ", "_").replace("-", "_")
    return asset_type in {"investment", "investment_portfolio", "portfolio", "stock", "stocks", "equity", "equities", "bond", "bonds", "fund", "funds", "etf", "etfs"}


def _inflation_factor(scenario, start_index, end_index):
    factor = 1.0
    direction = 1 if end_index >= start_index else -1
    for index in range(min(start_index, end_index), max(start_index, end_index)):
        year = index // 12
        rate = scenario.get("inflation_by_year", {}).get(str(year), scenario["annual_inflation"])
        factor *= (1 + rate) ** (direction / 12)
    return factor


def expense_terms_at(scenario, month, price_factor=None):
    phase = next(phase for phase in scenario["phases"] if phase["start_month"] <= month
                 and (phase["end_month"] is None or month < phase["end_month"]))
    current_date = date.fromisoformat(_date_at(scenario["start_date"], month))
    start_date = date.fromisoformat(scenario["start_date"])
    current_index = current_date.year * 12 + current_date.month - 1
    start_index = start_date.year * 12 + start_date.month - 1
    year_start = current_date.year * 12
    active = [term for term in scenario.get("expense_terms", [])
              if term["start_month"] <= month and (term["end_month"] is None or month < term["end_month"])]
    term = max(enumerate(active), key=lambda entry: (entry[1]["start_month"], entry[0]))[1] if active else None
    if term:
        anchor = date.fromisoformat(_date_at(scenario["start_date"], term["start_month"]))
        anchor_index = anchor.year * 12
        amount = max(0, term["monthly_expenses_chf"] + phase["monthly_expenses_chf"] - term["phase_expenses_chf"])
        exempt = term["inflation_exempt"]
        beginning = amount if exempt else amount * _inflation_factor(scenario, anchor_index, year_start)
        duration = None if term["end_month"] is None else term["end_month"] - month
    else:
        exempt = False
        factor = _inflation_factor(scenario, start_index, current_index) if price_factor is None else price_factor
        beginning = phase["monthly_expenses_chf"] * factor / _inflation_factor(scenario, year_start, current_index)
        duration = None
    amount = beginning if exempt else beginning * _inflation_factor(scenario, year_start, current_index)
    return {"year": current_date.year, "monthly_expenses_chf": beginning, "year_inflation": scenario.get("inflation_by_year", {}).get(str(current_date.year)),
            "inflation_exempt": exempt, "duration_months": duration, "actual_monthly_expenses_chf": amount}


def _state(cash, assets, debts, flow, preferences, scenario, month, price_factor):
    asset_total = cash + sum(asset["value_chf"] for asset in assets)
    debt_total = sum(debt["principal_chf"] for debt in debts)
    invested = sum(asset["value_chf"] for asset in assets
                   if is_investment_asset(asset))
    return {
        "cash_chf": cash,
        "assets": assets,
        "debts": debts,
        "total_assets_chf": asset_total,
        "total_debt_chf": debt_total,
        "net_worth_chf": asset_total - debt_total,
        "cash_flow": flow,
        "risk_exposure": {
            "investment_value_chf": invested,
            "investment_share_of_assets": invested / asset_total if asset_total > 0 else None,
            "cash_shortfall_chf": max(0.0, -cash),
            "annual_volatility_chf": sum(asset["value_chf"] * asset["annual_volatility"] for asset in assets),
        },
        "active_variables": {"preferences": deepcopy(preferences), "annual_inflation": scenario["annual_inflation"],
                             "absolute_month": month, "price_factor": price_factor},
    }


def create_root(scenario):
    scenario = _scenario(scenario)
    state = _state(scenario["opening_cash_chf"], deepcopy(scenario["assets"]), deepcopy(scenario["debts"]),
                   {"income_chf": 0.0, "expenses_chf": 0.0, "one_off_costs_chf": 0.0,
                    "debt_payments_chf": 0.0, "debt_interest_chf": 0.0, "investment_return_chf": 0.0,
                    "investment_contribution_chf": 0.0, "operating_cash_flow_chf": 0.0,
                    "net_cash_flow_chf": 0.0},
                   scenario["preferences"], scenario, 0, 1.0)
    state["active_variables"]["expense_terms"] = expense_terms_at(scenario, 0)
    state["active_variables"]["phase"] = next(
        (deepcopy(phase) for phase in scenario["phases"] if phase["start_month"] == 0), None)
    return {"id": _id(scenario), "parent_id": None, "branch_id": "root", "month": 0,
            "date": scenario["start_date"], "event_summary": "Initial financial state",
            "state": state, "scenario": scenario}


def _allocate_surplus(cash, net_flow, expenses, assets, preferences):
    reserve = expenses * preferences["emergency_months"] * preferences["safety_priority"]
    contribution = min(max(0.0, net_flow) * preferences["savings_rate"], max(0.0, cash - reserve))
    contribution *= preferences["risk_tolerance"] * (1 - preferences["safety_priority"])
    if contribution:
        portfolio = next((asset for asset in assets if asset["id"] == "__contributions__"), None)
        if portfolio is None:
            portfolio = {"id": "__contributions__", "type": "investment", "value_chf": 0.0,
                         "annual_return": preferences["investment_annual_return"],
                         "annual_volatility": preferences["investment_annual_volatility"]}
            assets.append(portfolio)
        portfolio["value_chf"] += contribution
    return contribution


def simulate(node, months, *, preferences=None, branch_id="main", scenario_updates=None):
    _month(months, "months")
    if not isinstance(branch_id, str) or not branch_id:
        raise ValueError("branch_id must be a non-empty string")
    scenario = deepcopy(node["scenario"])
    if scenario_updates:
        allowed = {"phases", "one_off_costs", "annual_inflation"}
        if set(scenario_updates) - allowed:
            raise ValueError("scenario_updates may replace phases, one_off_costs or annual_inflation")
        scenario.update(deepcopy(scenario_updates))
    scenario = _scenario(scenario)
    scenario_snapshot = pickle.dumps(scenario, protocol=pickle.HIGHEST_PROTOCOL)
    active_preferences = deepcopy(node["state"]["active_variables"]["preferences"])
    if preferences is not None:
        active_preferences.update(preferences)
    active_preferences = _preferences(active_preferences)
    cash = node["state"]["cash_chf"]
    assets = deepcopy(node["state"]["assets"])
    debts = deepcopy(node["state"]["debts"])
    asset_values = np.array([asset["value_chf"] for asset in assets], dtype=float)
    asset_growth = np.array([(1 + asset["annual_return"]) ** (1 / 12) - 1 for asset in assets], dtype=float)
    debt_values = np.array([debt["principal_chf"] for debt in debts], dtype=float)
    debt_rates = np.array([debt["annual_interest_rate"] for debt in debts], dtype=float)
    debt_payments = np.array([debt["monthly_payment_chf"] for debt in debts], dtype=float)
    contribution_index = next((i for i, asset in enumerate(assets) if asset["id"] == "__contributions__"), None)
    if contribution_index is not None:
        assets[contribution_index]["annual_return"] = active_preferences["investment_annual_return"]
        assets[contribution_index]["annual_volatility"] = active_preferences["investment_annual_volatility"]
        asset_growth[contribution_index] = (1 + active_preferences["investment_annual_return"]) ** (1 / 12) - 1
    price_factor = node["state"]["active_variables"]["price_factor"]
    output = []
    parent_id = node["id"]
    for month in range(node["month"], node["month"] + months):
        matching = [phase for phase in scenario["phases"] if phase["start_month"] <= month
                    and (phase["end_month"] is None or month < phase["end_month"])]
        if not matching:
            raise ValueError(f"No phase covers month {month}")
        phase = matching[0]
        # Phase income is in today's money and follows the price index like living costs, so inflation
        # (including year-specific rates) does not silently erode a salary. One-offs and loan payments stay nominal.
        income = phase["monthly_income_chf"] * price_factor
        terms = expense_terms_at(scenario, month, price_factor)
        expenses = terms["actual_monthly_expenses_chf"]
        costs = [item for item in scenario["one_off_costs"] if item["month"] == month]
        cost = sum(item["amount_chf"] for item in costs)
        accrued = debt_values * debt_rates / 12
        owed = debt_values + accrued
        paid = np.minimum(owed, debt_payments)
        debt_values = owed - paid
        interest = reduce(add, accrued.tolist(), 0.0)
        payments = reduce(add, paid.tolist(), 0.0)
        growth = asset_values * asset_growth
        asset_values += growth
        returns = reduce(add, growth.tolist(), 0.0)
        for debt, value in zip(debts, debt_values.tolist()):
            debt["principal_chf"] = value
        for asset, value in zip(assets, asset_values.tolist()):
            asset["value_chf"] = value
        net_flow = income - expenses - cost - payments
        cash += net_flow
        contribution = _allocate_surplus(cash, net_flow, expenses, assets, active_preferences)
        if contribution:
            if contribution_index is None:
                contribution_index = len(assets) - 1
                asset_values = np.append(asset_values, assets[contribution_index]["value_chf"])
                asset_growth = np.append(asset_growth, (1 + active_preferences["investment_annual_return"]) ** (1 / 12) - 1)
            else:
                asset_values[contribution_index] = assets[contribution_index]["value_chf"]
        cash -= contribution
        flow = {"income_chf": income, "expenses_chf": expenses, "one_off_costs_chf": cost,
                "debt_payments_chf": payments, "debt_interest_chf": interest,
                "investment_return_chf": returns, "investment_contribution_chf": contribution,
                "operating_cash_flow_chf": net_flow, "net_cash_flow_chf": net_flow - contribution}
        current_year = date.fromisoformat(_date_at(scenario["start_date"], month)).year
        rate = scenario.get("inflation_by_year", {}).get(str(current_year), scenario["annual_inflation"])
        price_factor *= (1 + rate) ** (1 / 12)
        state = _state(cash, deepcopy(assets), deepcopy(debts), flow, active_preferences, scenario,
                       month + 1, price_factor)
        state["active_variables"]["phase"] = deepcopy(phase)
        state["active_variables"]["expense_terms"] = terms
        events = [str(item.get("description", item.get("name", "One-off cost"))) for item in costs]
        if phase["start_month"] == month:
            events.insert(0, str(phase.get("name", "Phase started")))
        child = {"id": _id([parent_id, branch_id, month + 1, scenario, active_preferences]),
                 "parent_id": parent_id, "branch_id": branch_id, "month": month + 1,
                 "date": _date_at(scenario["start_date"], month + 1),
                 "event_summary": "; ".join(events) if events else "Monthly update",
                 "state": state, "scenario": pickle.loads(scenario_snapshot)}
        output.append(child)
        parent_id = child["id"]
    return output
