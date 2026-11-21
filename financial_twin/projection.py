"""Deterministic projection engine for a user-entered financial profile.

Pipeline:
    raw user input ──resolve_profile──► values tagged user / dataset / assumption
    decisions      ──resolve_event────► typed events with parameters and a start month
    _build()       ──────────────────► monthly arrays (income, costs, one-offs, deposits)
    run_arrays()   ──────────────────► C(t+1) = C(t) + I(t) − E(t) − K(t) − debt payments − deposits
    _metrics() / _compare()        ──► liquidity, goals and trade-offs vs. the baseline

Month 0 is the resolved start month. Point k of a timeline is the
state after k monthly transitions. Resolved records preserve supplied values,
dataset defaults, assumptions and dated milestone references for replay.
"""
from __future__ import annotations

from datetime import date
from copy import deepcopy
import json
import math
from pathlib import Path
from .debt import advance_loan, fixed_payment

DEFAULTS = json.loads(Path(__file__).with_name("defaults.json").read_text(encoding="utf-8"))

EMPLOYMENT_STATUSES = ("employed", "self_employed", "part_time", "student", "unemployed", "retired")
HOUSEHOLD_TYPES = ("single", "couple", "family", "shared_housing")
PRIORITIES = {"high": 0, "medium": 1, "low": 2}
SOURCES = ("user", "dataset", "assumption")
BAND = 0.15                     # ± applied to non-user values for the uncertainty band
MAX_SCENARIOS = 5
MAX_EVENTS = 10
BASELINE = "Baseline"


def _group(employment):
    groups = DEFAULTS["by_employment_status"]
    return groups.get(employment, groups["all"])


def _field(label, unit, lo, hi, default=None, source="assumption", direction=0,
           integer=False, choices=None, required=False, note=""):
    return {"label": label, "unit": unit, "min": lo, "max": hi, "default": default,
            "source": "user" if required else source, "direction": direction, "integer": integer,
            "choices": choices, "required": required, "note": note}


# direction: +1 income-like, −1 cost-like, 0 not varied in the uncertainty band.
PROFILE_FIELDS = {
    "minimum_reserve_chf": _field("Minimum cash reserve", "CHF", 0, 1e9, lambda g: 0.0),
    "debt_service_ratio_limit": _field("Debt service ratio limit", "share", 0, 10, lambda g: 0.36),
    "age": _field("Age", "years", 16, 100, integer=True, required=True),
    "cash_chf": _field("Cash savings", "CHF", -1e7, 1e8, required=True),
    "monthly_net_income_chf": _field("Monthly net income", "CHF", 0, 1e6, required=True),
    "monthly_rent_chf": _field("Monthly rent or housing cost", "CHF", 0, 1e5, required=True),
    "employment_status": _field("Employment status", None, None, None, lambda g: "employed",
                                choices=EMPLOYMENT_STATUSES, note="Selects dataset defaults"),
    "household_type": _field("Household", None, None, None, lambda g: "single",
                             choices=HOUSEHOLD_TYPES, note="Context only; does not change the maths"),
    "other_fixed_costs_chf": _field(
        "Other fixed costs (insurance, utilities, transport, telecom)", "CHF", 0, 1e5,
        lambda g: g["other_fixed_costs_chf"], "dataset", -1, note="Dataset median for this employment status"),
    "discretionary_chf": _field(
        "Discretionary spending (groceries, restaurants, retail, leisure, travel)", "CHF", 0, 1e5,
        lambda g: g["discretionary_chf"], "dataset", -1, note="Dataset median for this employment status"),
    "irregular_costs_chf": _field(
        "Irregular costs (monthly average)", "CHF", 0, 1e5,
        lambda g: g["irregular_costs_chf"], "dataset", -1, note="Dataset mean of unexpected expenses"),
    "bonus_share_of_income": _field(
        "Annual bonus as share of income", "share", 0, 1,
        lambda g: g["bonus_share_of_income"], "dataset", +1, note="Dataset mean, paid out monthly"),
    "investments_chf": _field("Investments", "CHF", 0, 1e9, lambda g: 0.0, note="None stated"),
    "investment_annual_return": _field("Annual investment return", "share", -0.5, 0.5, lambda g: 0.0,
                                       note="Zero unless the user chooses a return"),
    "retirement_age": _field("Retirement age", "years", 50, 75, lambda g: 65, integer=True,
                             note="Swiss AHV reference age"),
    "pension_share_of_income": _field("Pension as share of last income", "share", 0, 1.5, lambda g: 0.6,
                                      direction=+1, note="Illustrative replacement rate, not a pension estimate"),
}

EDU = DEFAULTS["education"]


def _p(label, unit, lo, hi, default, source="assumption", direction=0, integer=False, note=""):
    return _field(label, unit, lo, hi, default, source, direction, integer, note=note)


EVENT_TYPES = {
    "education": {
        "label": "Pursue a degree",
        "params": {
            "duration_months": _p("Study duration", "months", 1, 96, lambda p: EDU["study_duration_months"],
                                  "dataset", integer=True, note="Median S001 assumption"),
            "annual_tuition_chf": _p("Annual tuition", "CHF", 0, 1e5, lambda p: EDU["annual_tuition_chf"],
                                     "dataset", -1, note="Median S001 assumption"),
            "income_share_while_studying": _p(
                "Income kept while studying", "share", 0, 1,
                lambda p: 1.0 if p and p["employment_status"] == "student" else 0.2, direction=+1,
                note="Students keep current support; others keep a part-time job"),
            "income_after_chf": _p(
                "Monthly net income after graduating", "CHF", 0, 1e6,
                lambda p: round(p["monthly_net_income_chf"] * 1.15) if p else 0, direction=+1,
                note="Illustrative 15% increase from current income"),
        },
    },
    "career_change": {
        "label": "Change career",
        "params": {
            "gap_months": _p("Months without income", "months", 0, 36, lambda p: 3, integer=True),
            "income_share_during_gap": _p("Income kept during gap", "share", 0, 1, lambda p: 0.0, direction=+1),
            "income_change_pct": _p("Income change after gap", "share", -0.9, 3, lambda p: 0.15),
            "new_income_chf": _p("New monthly net income", "CHF", 0, 1e6, lambda p: None,
                                 note="If set, replaces the percentage change"),
            "one_off_cost_chf": _p("Retraining cost", "CHF", 0, 1e6, lambda p: 0.0, direction=-1),
        },
    },
    "relocation": {
        "label": "Relocate",
        "params": {
            "moving_cost_chf": _p("Moving cost", "CHF", 0, 1e6, lambda p: 1500, direction=-1,
                                  note="Illustrative moving cost assumption"),
            "rent_change_pct": _p("Rent change", "share", -0.9, 3, lambda p: 0.0),
            "income_change_pct": _p("Income change", "share", -0.9, 3, lambda p: 0.0),
            "cost_of_living_change_pct": _p("Other living costs change", "share", -0.9, 3, lambda p: 0.0),
        },
    },
    "housing_change": {
        "label": "Change housing",
        "params": {
            "purchase_price_chf": _p("Purchase price", "CHF", 0, 1e8, lambda p: 0),
            "down_payment_share": _p("Down payment", "share", 0, 1, lambda p: 0.2),
            "purchase_fees_share": _p("Purchase fees", "share", 0, 1, lambda p: 0.05),
            "mortgage_interest_rate": _p("Mortgage interest rate", "share", 0, 1, lambda p: 0.02),
            "detailed_housing": _p("Detailed housing accounting", "enabled", 0, 1, lambda p: 0, integer=True),
            "mortgage_term_months": _p("Mortgage term", "months", 1, 1200, lambda p: 360, integer=True),
            "annual_maintenance_share": _p("Annual maintenance", "share", 0, 1, lambda p: 0.01),
            "new_rent_chf": _p("New monthly rent", "CHF", 0, 1e5,
                               lambda p: p["monthly_rent_chf"] if p else None, note="Defaults to current rent"),
            "deposit_months": _p("Rent deposit", "months", 0, 3, lambda p: 3,
                                 note="Swiss law caps deposits at 3 months; old deposit refund not modelled"),
            "moving_cost_chf": _p("Moving cost", "CHF", 0, 1e6, lambda p: 1500, direction=-1),
        },
    },
    "unexpected_expense": {
        "label": "Unexpected expense",
        "params": {
            "amount_chf": _p("Amount", "CHF", 0, 1e7, lambda p: DEFAULTS["unexpected_event_amount_chf"],
                             "dataset", -1, note="Median unexpected expense in the dataset"),
        },
    },
    "custom": {
        "label": "Custom change",
        "params": {
            "monthly_income_change_chf": _p("Monthly income change", "CHF", -1e6, 1e6, lambda p: 0.0),
            "monthly_expense_change_chf": _p("Monthly expense change", "CHF", -1e6, 1e6, lambda p: 0.0),
            "one_off_cost_chf": _p("One-off cost", "CHF", 0, 1e7, lambda p: 0.0, direction=-1),
            "duration_months": _p("Duration (0 = permanent)", "months", 0, 600, lambda p: 0, integer=True),
        },
    },
}


# ── validation helpers ──────────────────────────────────────────────────────

def _number(value, name, spec):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if spec.get("integer"):
        if value != int(value):
            raise ValueError(f"{name} must be a whole number")
        value = int(value)
    else:
        value = float(value)
    lo, hi = spec.get("min"), spec.get("max")
    if lo is not None and value < lo or hi is not None and value > hi:
        raise ValueError(f"{name} must be between {lo:g} and {hi:g}")
    return value


def _month_key(text, name):
    if not isinstance(text, str):
        raise ValueError(f"{name} must be a YYYY-MM string")
    try:
        year, month = (int(part) for part in text[:7].split("-"))
        date(year, month, 1)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name} must be a YYYY-MM string") from exc
    return year, month


def _add_months(start, months):
    year, month = divmod(start[0] * 12 + start[1] - 1 + months, 12)
    return f"{year:04d}-{month + 1:02d}"


def _months_between(start, end):
    return (end[0] - start[0]) * 12 + end[1] - start[1]


def _month_index(raw, start, name):
    """Accept {'start_month': int} / {'in_months': int} or {'date': 'YYYY-MM'}."""
    for key in ("start_month", "in_months"):
        if key in raw:
            return _number(raw[key], f"{name} {key}", {"integer": True, "min": 0, "max": 1200})
    if "date" in raw:
        index = _months_between(start, _month_key(raw["date"], f"{name} date"))
        if index < 0:
            raise ValueError(f"{name} date is before the projection start")
        return index
    return 0


def _check_keys(raw, allowed, name):
    if not isinstance(raw, dict):
        raise ValueError(f"{name} must be an object")
    unknown = set(raw) - set(allowed)
    if unknown:
        raise ValueError(f"Unknown {name} fields: {sorted(unknown)}")


def _value(value, source, spec):
    return {"value": value, "source": source, "label": spec["label"], "unit": spec["unit"],
            "as_of_date": None, "frequency": None, "transformation": "identity",
            **({"note": spec["note"]} if spec["note"] and source != "user" else {})}


def _frequency(key):
    if key.startswith("monthly_") or key.endswith("_monthly"):
        return "monthly"
    if key.startswith("annual_") or "annual" in key:
        return "annual"
    return None


# ── input resolution ───────────────────────────────────────────────────────

def resolve_profile(raw, start=(2025, 1)):
    """Validate user input and fill gaps from dataset defaults or labelled assumptions."""
    _check_keys(raw, [*PROFILE_FIELDS, "name", "debts", "goals"], "profile")
    missing = [key for key, spec in PROFILE_FIELDS.items() if spec["required"] and raw.get(key) is None]
    if missing:
        raise ValueError(f"Missing required profile fields: {missing}")
    employment = raw.get("employment_status") or "employed"
    if employment not in EMPLOYMENT_STATUSES:
        raise ValueError(f"employment_status must be one of {list(EMPLOYMENT_STATUSES)}")
    group = _group(employment)
    fields = {}
    for key, spec in PROFILE_FIELDS.items():
        given = raw.get(key)
        if given is None:
            fields[key] = _value(spec["default"](group), spec["source"], spec)
        elif spec["choices"]:
            if given not in spec["choices"]:
                raise ValueError(f"{key} must be one of {list(spec['choices'])}")
            fields[key] = _value(given, "user", spec)
        else:
            fields[key] = _value(_number(given, key, spec), "user", spec)
        fields[key]["frequency"] = _frequency(key)

    debts = []
    for index, debt in enumerate(raw.get("debts") or []):
        _check_keys(debt, ["name", "principal_chf", "annual_interest_rate", "monthly_payment_chf", "term_months"], "debt")
        loan = {
            "name": str(debt.get("name") or f"Debt {index + 1}")[:80],
            "principal_chf": _number(debt.get("principal_chf"), "debt principal_chf", {"min": 0, "max": 1e9}),
            "annual_interest_rate": _number(debt.get("annual_interest_rate", 0), "annual_interest_rate",
                                            {"min": 0, "max": 0.5}),
            "monthly_payment_chf": _number(debt.get("monthly_payment_chf", 0), "monthly_payment_chf",
                                           {"min": 0, "max": 1e7}),
        }
        if "term_months" in debt:
            loan["term_months"] = _number(debt["term_months"], "term_months", {"min": 1, "max": 1200, "integer": True})
            loan["monthly_payment_chf"] = fixed_payment(loan["principal_chf"], loan["annual_interest_rate"], loan["term_months"])
        debts.append(loan)
    goals = []
    for index, goal in enumerate(raw.get("goals") or []):
        _check_keys(goal, ["name", "target_chf", "in_months", "date", "priority"], "goal")
        priority = goal.get("priority", "medium")
        if priority not in PRIORITIES:
            raise ValueError(f"goal priority must be one of {list(PRIORITIES)}")
        if "in_months" not in goal and "date" not in goal:
            raise ValueError("goal needs in_months or date")
        month = _month_index(goal, start, "goal")
        goals.append({"name": str(goal.get("name") or f"Goal {index + 1}")[:80],
                      "target_chf": _number(goal.get("target_chf"), "goal target_chf", {"min": 0, "max": 1e9}),
                      "target_month": month, "target_date": _add_months(start, month), "priority": priority})
    return {"name": str(raw.get("name") or "You")[:80], "fields": fields, "debts": debts, "goals": goals}


def _values(profile):
    return {key: item["value"] for key, item in profile["fields"].items()}


def resolve_event(raw, profile, start, *, frozen=False):
    _check_keys(raw, ["type", "label", "start_month", "in_months", "date", "params", "resolved_params"], "event")
    kind = raw.get("type")
    if kind not in EVENT_TYPES:
        raise ValueError(f"event type must be one of {list(EVENT_TYPES)}")
    spec = EVENT_TYPES[kind]
    params_raw = raw.get("params") or {}
    _check_keys(params_raw, spec["params"], f"{kind} params")
    resolved = raw.get("resolved_params") or {}
    p = _values(profile)
    params = {}
    for key, pspec in spec["params"].items():
        given = params_raw.get(key)
        if frozen and key in params_raw and given is None:
            if kind != "career_change" or key != "new_income_chf":
                raise ValueError("Invalid null scenario record parameter")
            params[key] = _value(None, "assumption", pspec)
        elif given is None and key in resolved.get("params", {}):
            params[key] = _value(_number(resolved["params"][key], key, pspec), resolved["source"], pspec)
            params[key]["source_urls"] = resolved.get("source_urls", [])
            params[key]["note"] = resolved.get("note", "")
            params[key]["as_of_date"] = resolved.get("as_of_date")
        elif given is None:
            params[key] = _value(pspec["default"](p), pspec["source"], pspec)
        else:
            params[key] = _value(_number(given, key, pspec), "user", pspec)
        params[key]["frequency"] = _frequency(key)
    month = _month_index(raw, start, kind)
    return {"type": kind, "label": str(raw.get("label") or spec["label"])[:80],
            "start_month": month, "date": _add_months(start, month), "params": params}


# ── monthly arrays ─────────────────────────────────────────────────────────

def _adjusted(item, spec, factor):
    """Scale a non-user value by ±BAND in the direction that hurts (factor −1) or helps (+1)."""
    value = item["value"]
    if factor == 0 or item["source"] == "user" or not spec["direction"] or isinstance(value, str):
        return value
    return value * (1 + factor * spec["direction"] * BAND)


def _build(profile, events, horizon, factor=0, trace=True):
    p = {key: _adjusted(item, PROFILE_FIELDS[key], factor) for key, item in profile["fields"].items()}
    salary = [p["monthly_net_income_chf"]] * horizon
    rent = [p["monthly_rent_chf"]] * horizon
    fixed = [p["other_fixed_costs_chf"]] * horizon
    disc = [p["discretionary_chf"]] * horizon
    irregular = [p["irregular_costs_chf"]] * horizon
    one_off = [0.0] * horizon
    deposit = [0.0] * horizon
    purchases = [[] for _ in range(horizon)]
    marks = [[] for _ in range(horizon)]
    precedence = []

    for input_index, event in sorted(enumerate(events), key=lambda pair: (pair[1]["start_month"], pair[0])):
        m0 = event["start_month"]
        if m0 >= horizon:
            if trace:
                precedence.append({"order": len(precedence), "input_index": input_index, "type": event["type"],
                                   "label": event["label"], "start_month": m0, "changes": [],
                                   "ignored_reason": "starts_after_horizon"})
            continue
        tracked = {"monthly_income_before_bonus_chf": salary, "housing_cost_chf": rent, "fixed_costs_chf": fixed,
                   "discretionary_chf": disc, "irregular_costs_chf": irregular,
                   "one_off_chf": one_off, "deposit_chf": deposit}
        before = {key: values[:] for key, values in tracked.items()} if trace else None
        spec = EVENT_TYPES[event["type"]]["params"]
        q = {key: _adjusted(item, spec[key], factor) for key, item in event["params"].items()}
        marks[m0].append(event["label"])
        after = range(m0, horizon)
        if event["type"] == "education":
            end = min(horizon, m0 + q["duration_months"])
            for t in range(m0, end):
                salary[t] *= q["income_share_while_studying"]
                fixed[t] += q["annual_tuition_chf"] / 12
            for t in range(end, horizon):
                salary[t] = q["income_after_chf"]
            if end < horizon:
                marks[end].append("Graduation")
        elif event["type"] == "career_change":
            end = min(horizon, m0 + q["gap_months"])
            for t in range(m0, end):
                salary[t] *= q["income_share_during_gap"]
            for t in range(end, horizon):
                if q["new_income_chf"] is None:
                    salary[t] *= 1 + q["income_change_pct"]
                else:
                    salary[t] = q["new_income_chf"]
            one_off[m0] += q["one_off_cost_chf"]
        elif event["type"] == "relocation":
            one_off[m0] += q["moving_cost_chf"]
            for t in after:
                rent[t] *= 1 + q["rent_change_pct"]
                salary[t] *= 1 + q["income_change_pct"]
                fixed[t] *= 1 + q["cost_of_living_change_pct"]
                disc[t] *= 1 + q["cost_of_living_change_pct"]
        elif event["type"] == "housing_change":
            for t in after:
                if q["purchase_price_chf"]:
                    interest_share = 0 if q["detailed_housing"] else (1 - q["down_payment_share"]) * q["mortgage_interest_rate"]
                    rent[t] = q["purchase_price_chf"] * (interest_share + q["annual_maintenance_share"]) / 12
                else:
                    rent[t] = q["new_rent_chf"]
            if q["purchase_price_chf"]:
                deposit[m0] += q["purchase_price_chf"] * q["down_payment_share"]
                one_off[m0] += q["purchase_price_chf"] * q["purchase_fees_share"]
                if q["detailed_housing"]:
                    purchases[m0].append({"name": event["label"], "price_chf": q["purchase_price_chf"],
                        "down_payment_chf": q["purchase_price_chf"] * q["down_payment_share"],
                        "fees_chf": q["purchase_price_chf"] * q["purchase_fees_share"],
                        "annual_interest_rate": q["mortgage_interest_rate"], "term_months": q["mortgage_term_months"]})
            else:
                deposit[m0] += q["deposit_months"] * q["new_rent_chf"]
            one_off[m0] += q["moving_cost_chf"]
        elif event["type"] == "unexpected_expense":
            one_off[m0] += q["amount_chf"]
        elif event["type"] == "custom":
            end = horizon if q["duration_months"] == 0 else min(horizon, m0 + q["duration_months"])
            for t in range(m0, end):
                salary[t] += q["monthly_income_change_chf"]
                fixed[t] += q["monthly_expense_change_chf"]
            one_off[m0] += q["one_off_cost_chf"]

        if trace:
            changes = []
            for field, values in tracked.items():
                current = None
                for t, (old, new) in enumerate(zip(before[field], values)):
                    if old == new:
                        current = None
                        continue
                    if field in ("one_off_chf", "deposit_chf") or event["type"] == "custom" or event["type"] == "education" and field == "fixed_costs_chf":
                        operation = "add"
                    elif event["type"] == "housing_change" or event["type"] == "education" and field == "monthly_income_before_bonus_chf" and t >= m0 + q["duration_months"] or event["type"] == "career_change" and field == "monthly_income_before_bonus_chf" and t >= m0 + q["gap_months"] and q["new_income_chf"] is not None:
                        operation = "replace"
                    else:
                        operation = "multiply"
                    if current and current["end_month"] == t and current["before_chf"] == old and current["after_chf"] == new and current["operation"] == operation:
                        current["end_month"] = t + 1
                    else:
                        current = {"field": field, "operation": operation, "start_month": t,
                                   "end_month": t + 1, "before_chf": old, "after_chf": new}
                        changes.append(current)
            precedence.append({"order": len(precedence), "input_index": input_index, "type": event["type"],
                               "label": event["label"], "start_month": m0, "changes": changes})

    # Retirement applies in every path, after decisions have shaped the salary.
    retire_at = None
    if p["employment_status"] != "retired" and p["age"] < p["retirement_age"]:
        months = math.ceil((p["retirement_age"] - p["age"]) * 12)
        if months < horizon:
            retire_at = months
            pension = max(0.0, salary[months - 1]) * p["pension_share_of_income"]
            before_retirement = salary[:] if trace else None
            for t in range(months, horizon):
                salary[t] = pension
            marks[months].append(f"Retirement at {p['retirement_age']}")
            if trace:
                changes = []
                for t in range(months, horizon):
                    if before_retirement[t] == salary[t]:
                        continue
                    if changes and changes[-1]["end_month"] == t and changes[-1]["before_chf"] == before_retirement[t]:
                        changes[-1]["end_month"] = t + 1
                    else:
                        changes.append({"field": "monthly_income_before_bonus_chf", "operation": "replace", "start_month": t,
                                        "end_month": t + 1, "before_chf": before_retirement[t], "after_chf": pension})
                precedence.append({"order": len(precedence), "input_index": None, "type": "retirement",
                                   "label": f"Retirement at {p['retirement_age']}", "start_month": months, "changes": changes})

    bonus = p["bonus_share_of_income"]
    income = [max(0.0, s) * (1 if retire_at is not None and t >= retire_at else 1 + bonus)
              for t, s in enumerate(salary)]
    expenses = [max(0.0, rent[t] + fixed[t] + disc[t] + irregular[t]) for t in range(horizon)]
    return {"income": income, "expenses": expenses, "one_off": one_off, "deposit": deposit,
            "marks": marks, "purchases": purchases, "event_precedence": precedence, "investments": p["investments_chf"],
            "investment_annual_return": p["investment_annual_return"], "cash": p["cash_chf"]}


# ── core recurrence ────────────────────────────────────────────────────────

def run_arrays(opening_cash, income, expenses, one_off=None, deposit=None, *, investments=0.0,
               investment_annual_return=0.0, debts=(), start=(2025, 1), marks=None,
               minimum_reserve_chf=0.0, debt_service_ratio_limit=0.36, purchases=None,
               annual_inflation=0.0, inflation_by_year=None):
    """C(t+1) = C(t) + I(t) − E(t) − K(t) − debt payments − deposits moved out of cash."""
    horizon = len(income)
    one_off = one_off or [0.0] * horizon
    deposit = deposit or [0.0] * horizon
    marks = marks or [[] for _ in range(horizon)]
    purchases = purchases or [[] for _ in range(horizon)]
    growth = (1 + investment_annual_return) ** (1 / 12) - 1
    loans = [dict(debt) for debt in debts]
    cash, held, locked, property_value = float(opening_cash), float(investments), 0.0, 0.0
    price_factor = 1.0
    inflation_by_year = inflation_by_year or {}

    def point(month, flows, low):
        debt = sum(loan["principal_chf"] for loan in loans)
        upcoming = expenses[min(month, horizon - 1)] if horizon else 0
        ratio = flows["debt_payment_chf"] / flows["income_chf"] if flows["income_chf"] > 0 else None
        return {"month": month, "date": _add_months(start, month),
                **{key: value if key in ("budget_breach", "debt_breakdown", "housing_purchases") else round(value, 2) for key, value in flows.items()},
                "minimum_reserve_breach": low < minimum_reserve_chf,
                "minimum_reserve_gap_chf": round(max(0.0, minimum_reserve_chf - low), 2),
                "debt_service_ratio": ratio,
                "debt_service_affordable": ratio <= debt_service_ratio_limit if ratio is not None else None,
                "debt_service_affordability_status": ("undefined_zero_income" if ratio is None else
                    "within_limit" if ratio <= debt_service_ratio_limit else "above_limit"),
                "cash_chf": round(cash, 2), "low_point_chf": round(low, 2),
                "base_cpi": 100.0, "cpi_base_date": _add_months(start, 0), "cpi_unit": "index points",
                "cpi": round(100 * price_factor, 8), "price_factor": round(price_factor, 10),
                "real_cash_chf": round(cash / price_factor, 2),
                "investments_chf": round(held, 2), "deposits_chf": round(locked, 2),
                "property_asset_chf": round(property_value, 2),
                "mortgage_liability_chf": round(sum(loan["principal_chf"] for loan in loans if loan.get("type") == "mortgage"), 2),
                "debt_chf": round(debt, 2), "net_worth_chf": round(cash + held + locked + property_value - debt, 2),
                "real_net_worth_chf": round((cash + held + locked + property_value - debt) / price_factor, 2),
                "runway_months": round(max(cash, 0) / upcoming, 1) if upcoming > 0 else None,
                "events": list(marks[month]) if month < horizon else []}

    zero = dict.fromkeys(("income_chf", "expenses_chf", "one_off_chf", "deposit_chf", "debt_payment_chf",
                          "debt_interest_chf", "investment_return_chf", "net_cash_flow_chf",
                          "cash_change_after_transfers_chf", "budget_breach"), 0.0)
    zero.update({"down_payment_chf": 0.0, "purchase_fees_chf": 0.0, "mortgage_principal_paid_chf": 0.0})
    zero["budget_breach"] = False
    zero["debt_breakdown"] = []
    zero["housing_purchases"] = []
    rows = [point(0, zero, cash)]
    for t in range(horizon):
        opening = cash
        low = cash - one_off[t] - deposit[t]
        for purchase in purchases[t]:
            property_value += purchase["price_chf"]
            principal = purchase["price_chf"] - purchase["down_payment_chf"]
            loans.append({"name": purchase["name"], "type": "mortgage", "principal_chf": principal,
                          "annual_interest_rate": purchase["annual_interest_rate"],
                          "monthly_payment_chf": fixed_payment(principal, purchase["annual_interest_rate"], purchase["term_months"])})
        breakdown = [advance_loan(loan) for loan in loans]
        interest = sum(item["interest_accrued_chf"] for item in breakdown)
        payment = sum(item["payment_chf"] for item in breakdown)
        returned = held * growth
        held += returned
        net = income[t] - expenses[t] - one_off[t] - payment
        cash += net - deposit[t]
        year = int(_add_months(start, t)[:4])
        price_factor *= (1 + inflation_by_year.get(str(year), annual_inflation)) ** (1 / 12)
        locked += deposit[t] - sum(purchase["down_payment_chf"] for purchase in purchases[t])
        flows = {"income_chf": income[t], "expenses_chf": expenses[t], "one_off_chf": one_off[t],
                 "deposit_chf": deposit[t], "debt_payment_chf": payment, "debt_interest_chf": interest,
                 "investment_return_chf": returned, "net_cash_flow_chf": net,
                 "cash_change_after_transfers_chf": round(round(cash, 2) - round(opening, 2), 2),
                 "budget_breach": expenses[t] + one_off[t] + deposit[t] + payment > income[t] + max(opening, 0),
                 "down_payment_chf": sum(purchase["down_payment_chf"] for purchase in purchases[t]),
                 "purchase_fees_chf": sum(purchase["fees_chf"] for purchase in purchases[t]),
                 "mortgage_principal_paid_chf": sum(item["principal_paid_chf"] for item in breakdown if item["type"] == "mortgage"),
                 "debt_breakdown": breakdown, "housing_purchases": purchases[t]}
        rows.append(point(t + 1, flows, min(low, cash)))
    return rows


# ── metrics ────────────────────────────────────────────────────────────────

def evaluate_goals(rows, goals):
    horizon = rows[-1]["month"]
    committed = 0.0
    results = []
    for goal in sorted(goals, key=lambda g: (PRIORITIES[g["priority"]], g["target_month"])):
        prior = committed
        attained = next((row for row in rows if row["cash_chf"] - prior >= goal["target_chf"]), None)
        details = {"prior_reserved_cash_chf": round(prior, 2), "reserved_cash_chf": 0.0,
                   "first_attainment_month": attained["month"] if attained else None,
                   "first_attainment_date": attained["date"] if attained else None}
        if goal["target_month"] > horizon:
            results.append({**goal, "met": None, "progress": None, "cash_at_target_chf": None,
                            **details, "deadline_gap_chf": None, "failure_reason": "deadline_beyond_horizon",
                            "note": "Target date is beyond the projection horizon"})
            continue
        target = next(row for row in rows if row["month"] == goal["target_month"])
        available = target["cash_chf"] - committed
        met = available >= goal["target_chf"]
        if met:
            committed += goal["target_chf"]
            details["reserved_cash_chf"] = goal["target_chf"]
        progress = 1.0 if goal["target_chf"] == 0 else max(0.0, min(available / goal["target_chf"], 1.0))
        results.append({**goal, "met": met, "progress": round(progress, 3),
                        "cash_at_target_chf": round(available, 2), **details,
                        "deadline_gap_chf": round(max(0, goal["target_chf"] - available), 2),
                        "failure_reason": None if met else "insufficient_cash_at_deadline"})
    return results


def _metrics(rows, goals):
    flows = rows[1:]
    lowest = min(rows, key=lambda row: row["low_point_chf"])
    negative = [row for row in flows if row["cash_chf"] < 0]
    funding = [row for row in rows if row["low_point_chf"] < 0]
    reserve = [row for row in rows if row["minimum_reserve_breach"]]
    budget = [row for row in flows if row["budget_breach"]]
    income = sum(row["income_chf"] for row in flows)
    net = sum(row["net_cash_flow_chf"] for row in flows)
    horizon = len(flows)
    results = evaluate_goals(rows, goals)
    end = rows[-1]
    return {
        "first_funding_gap_month": funding[0]["month"] if funding else None,
        "first_funding_gap_date": funding[0]["date"] if funding else None,
        "first_funding_gap_period_start": rows[max(0, funding[0]["month"] - 1)]["date"] if funding else None,
        "maximum_funding_need_chf": round(max(0, -lowest["low_point_chf"]), 2),
        "minimum_reserve_breaches": [{"month": row["month"], "date": row["date"], "gap_chf": row["minimum_reserve_gap_chf"]} for row in reserve],
        "budget_breaches": [{"month": row["month"], "date": row["date"]} for row in budget],
        "closing_cash_chf": end["cash_chf"],
        "closing_net_worth_chf": end["net_worth_chf"],
        "closing_real_cash_chf": end["real_cash_chf"],
        "closing_real_net_worth_chf": end["real_net_worth_chf"],
        "min_cash_chf": lowest["low_point_chf"],
        "min_cash_month": lowest["month"],
        "min_cash_date": lowest["date"],
        "first_negative_month": negative[0]["month"] if negative else None,
        "first_negative_date": negative[0]["date"] if negative else None,
        "months_in_shortfall": len(negative),
        "average_monthly_net_cash_flow_chf": round(net / horizon, 2) if horizon else 0.0,
        "savings_rate": round(net / income, 4) if income > 0 else None,
        "closing_runway_months": end["runway_months"],
        "goals": results,
        "goals_met": sum(1 for goal in results if goal["met"]),
        "goals_evaluated": sum(1 for goal in results if goal["met"] is not None),
    }


def _compare(base, other):
    d_cash = [o["cash_chf"] - b["cash_chf"] for b, o in zip(base, other)]
    d_worth = [o["net_worth_chf"] - b["net_worth_chf"] for b, o in zip(base, other)]
    behind = [index for index, value in enumerate(d_worth) if value < -0.005]
    if not behind:
        break_even = 0
    elif behind[-1] == len(d_worth) - 1:
        break_even = None
    else:
        break_even = behind[-1] + 1
    deepest = min(range(len(d_cash)), key=d_cash.__getitem__)
    return {
        "cash_difference_at_end_chf": round(d_cash[-1], 2),
        "net_worth_difference_at_end_chf": round(d_worth[-1], 2),
        "largest_cash_gap_chf": round(d_cash[deepest], 2),
        "largest_cash_gap_month": deepest,
        "largest_cash_gap_date": other[deepest]["date"],
        "break_even_month": break_even,
        "break_even_date": other[break_even]["date"] if break_even is not None else None,
    }


# ── public API ─────────────────────────────────────────────────────────────

def _start(value):
    if value is None:
        today = date.today()
        return today.year, today.month
    return _month_key(value, "start")


def _assumptions(profile, paths):
    used = [{"scope": "Profile", "field": item["label"], "value": item["value"], "unit": item["unit"],
             "source": item["source"], "note": item.get("note", "")}
            for item in profile["fields"].values() if item["source"] != "user"]
    for name, path in paths.items():
        for event in path["events"]:
            used += [{"scope": f"{name}: {event['label']}", "field": item["label"], "value": item["value"],
                      "unit": item["unit"], "source": item["source"], "note": item.get("note", ""),
                      "source_urls": item.get("source_urls", [])}
                     for item in event["params"].values()
                     if item["source"] != "user" and item["value"] is not None]
    return used


def _lineage(value, source, unit=None, frequency=None, transformation="identity"):
    return {"value": value, "source": source, "as_of_date": None, "unit": unit,
            "frequency": frequency, "transformation": transformation}


def _record_items(items, raw_items, kind):
    records = []
    for index, item in enumerate(items):
        raw = raw_items[index]
        fields = {}
        for key, value in item.items():
            source = "user" if key in raw or key in ("target_month", "target_date") else "assumption"
            unit = "CHF" if key.endswith("_chf") else "share" if key.endswith("_rate") else "months" if key.endswith("_months") or key == "target_month" else None
            transformation = "fixed-term amortization" if kind == "debt" and key == "monthly_payment_chf" and "term_months" in raw else "identity"
            if transformation != "identity":
                source = "derived"
            fields[key] = _lineage(value, source, unit, _frequency(key), transformation)
        records.append(fields)
    return records


def _replay_inputs(record):
    _check_keys(record, ["version", "start", "horizon_years", "bands", "profile", "plans", "inflation", "debt_lineage", "goal_lineage", "inflation_coverage"], "scenario record")
    if record.get("version") != 1 or not isinstance(record.get("profile"), dict) or not isinstance(record.get("plans"), dict):
        raise ValueError("Invalid scenario record")
    profile = record["profile"]
    _check_keys(profile, ["name", "fields", "debts", "goals"], "record profile")
    fields = profile.get("fields")
    if not isinstance(fields, dict) or set(fields) != set(PROFILE_FIELDS):
        raise ValueError("Invalid scenario record fields")
    raw = {key: item["value"] for key, item in fields.items() if isinstance(item, dict) and "value" in item}
    if len(raw) != len(fields):
        raise ValueError("Invalid scenario record fields")
    raw["name"] = profile.get("name")
    raw["debts"] = profile.get("debts")
    goals = profile.get("goals")
    if not isinstance(goals, list):
        raise ValueError("Invalid scenario record goals")
    raw["goals"] = [{"name": goal["name"], "target_chf": goal["target_chf"],
                     "in_months": goal["target_month"], "priority": goal["priority"]} for goal in goals]
    plans = {}
    for name, events in record["plans"].items():
        if not isinstance(events, list):
            raise ValueError("Invalid scenario record events")
        plans[name] = [{"type": event["type"], "label": event["label"], "start_month": event["start_month"],
                        "params": {key: item["value"] for key, item in event["params"].items()}}
                       for event in events]
    return raw, plans


def _restore_lineage(validated, recorded):
    if set(validated) != set(recorded):
        raise ValueError("Invalid scenario record lineage")
    for key, item in validated.items():
        saved = recorded[key]
        if not isinstance(saved, dict) or saved.get("value") != item["value"]:
            raise ValueError("Invalid scenario record value")
        if not isinstance(saved.get("source"), str) or not saved["source"] or saved.get("unit") != item["unit"]:
            raise ValueError("Invalid scenario record source")
        if saved.get("as_of_date") is not None:
            try:
                date.fromisoformat(saved["as_of_date"])
            except (ValueError, TypeError) as exc:
                raise ValueError("Invalid scenario record source date") from exc
        if saved.get("frequency") is not None and not isinstance(saved["frequency"], str):
            raise ValueError("Invalid scenario record frequency")
        if not isinstance(saved.get("transformation"), str):
            raise ValueError("Invalid scenario record transformation")
        validated[key] = deepcopy(saved)


def _perturb(value, spec):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    step = abs(value) * 0.15
    if not step:
        step = 100 if spec["unit"] == "CHF" else 1 if spec["integer"] else 0.01
    if spec["integer"]:
        step = max(1, round(step))
    candidate = value + step
    if spec["max"] is not None and candidate > spec["max"]:
        candidate = value - step
    if spec["min"] is not None:
        candidate = max(spec["min"], candidate)
    if spec["max"] is not None:
        candidate = min(spec["max"], candidate)
    candidate = int(round(candidate)) if spec["integer"] else candidate
    return candidate if candidate != value else None


def project(profile, scenarios=None, *, horizon_years=10, start=None, bands=True,
            annual_inflation=None, inflation_by_year=None, scenario_record=None, profile_lineage=None):
    """Project a baseline plus up to five named decision paths from user inputs.

    profile    dict of user inputs (see PROFILE_FIELDS); unknown values are filled in.
    scenarios  {name: [event, ...]}; each event is {"type", "start_month"|"in_months"|"date", "params"}.
    """
    inflation_source = "assumption" if annual_inflation is None else "user"
    if scenario_record is not None:
        profile, scenarios = _replay_inputs(scenario_record)
        horizon_years = scenario_record.get("horizon_years")
        start = scenario_record.get("start")
        bands = scenario_record.get("bands")
        inflation = scenario_record.get("inflation")
        if not isinstance(inflation, dict):
            raise ValueError("Invalid scenario record inflation")
        annual_inflation = inflation.get("annual_inflation")
        inflation_by_year = inflation.get("inflation_by_year")
        inflation_source = inflation.get("source")
        if inflation_source not in ("user", "assumption"):
            raise ValueError("Invalid scenario record inflation source")
    if not isinstance(bands, bool):
        raise ValueError("bands must be a boolean")
    annual_inflation = _number(0 if annual_inflation is None else annual_inflation, "annual_inflation", {"min": -0.999999, "max": 10})
    if inflation_by_year is None:
        inflation_by_year = {}
    if not isinstance(inflation_by_year, dict):
        raise ValueError("inflation_by_year must be an object")
    for year, rate in inflation_by_year.items():
        if not isinstance(year, str) or len(year) != 4 or not year.isdigit():
            raise ValueError("inflation year must be a calendar year")
        _number(rate, "year inflation", {"min": -0.999999, "max": 10})
    horizon_years = _number(horizon_years, "horizon_years", {"integer": True, "min": 1, "max": 50})
    horizon = horizon_years * 12
    origin = _start(start)
    resolved = resolve_profile(profile, origin)
    if scenario_record is not None:
        _restore_lineage(resolved["fields"], scenario_record["profile"]["fields"])
    elif profile_lineage is not None:
        if not isinstance(profile_lineage, dict) or set(profile_lineage) - {"fields", "debts", "goals"}:
            raise ValueError("Invalid profile lineage")
        fields = profile_lineage.get("fields", {})
        if not isinstance(fields, dict) or set(fields) - set(resolved["fields"]):
            raise ValueError("Invalid profile lineage")
        for key, item in fields.items():
            field = {key: resolved["fields"][key]}
            _restore_lineage(field, {key: item})
            resolved["fields"][key] = field[key]
    scenarios = scenarios or {}
    if not isinstance(scenarios, dict) or len(scenarios) > MAX_SCENARIOS:
        raise ValueError(f"scenarios must map up to {MAX_SCENARIOS} names to event lists")
    if BASELINE in scenarios:
        raise ValueError(f"'{BASELINE}' is reserved for the no-decision path")
    warnings = []
    plans = {BASELINE: []}
    for name, events in scenarios.items():
        if not isinstance(name, str) or not name.strip() or len(name) > 80:
            raise ValueError("scenario names must be non-empty strings up to 80 characters")
        if not isinstance(events, list) or len(events) > MAX_EVENTS:
            raise ValueError(f"each scenario takes a list of up to {MAX_EVENTS} events")
        plans[name] = [resolve_event(event, resolved, origin, frozen=scenario_record is not None) for event in events]
        if scenario_record is not None:
            for event, recorded in zip(plans[name], scenario_record["plans"][name]):
                _restore_lineage(event["params"], recorded["params"])
        warnings += [f"{name}: '{event['label']}' starts after the horizon and is ignored"
                     for event in plans[name] if event["start_month"] >= horizon]

    def run(events, factor, *, run_profile=None, trace=True, run_inflation=None, run_inflation_by_year=None):
        current_profile = resolved if run_profile is None else run_profile
        current_inflation = annual_inflation if run_inflation is None else run_inflation
        current_overrides = inflation_by_year if run_inflation_by_year is None else run_inflation_by_year
        built = _build(current_profile, events, horizon, factor, trace=trace)
        rows = run_arrays(built["cash"], built["income"], built["expenses"], built["one_off"],
                          built["deposit"], investments=built["investments"],
                          investment_annual_return=built["investment_annual_return"],
                          debts=current_profile["debts"], start=origin, marks=built["marks"],
                          minimum_reserve_chf=current_profile["fields"]["minimum_reserve_chf"]["value"],
                          debt_service_ratio_limit=current_profile["fields"]["debt_service_ratio_limit"]["value"],
                          purchases=built["purchases"], annual_inflation=current_inflation,
                          inflation_by_year=current_overrides)
        return rows, built["event_precedence"]

    debt_lineage = _record_items(resolved["debts"], profile.get("debts") or [], "debt")
    goal_lineage = _record_items(resolved["goals"], profile.get("goals") or [], "goal")
    if scenario_record is not None:
        for expected, key in ((debt_lineage, "debt_lineage"), (goal_lineage, "goal_lineage")):
            saved = scenario_record.get(key)
            if not isinstance(saved, list) or len(saved) != len(expected):
                raise ValueError("Invalid scenario record lineage")
            for validated, recorded in zip(expected, saved):
                _restore_lineage(validated, recorded)
    elif profile_lineage is not None:
        for expected, key in ((debt_lineage, "debts"), (goal_lineage, "goals")):
            supplied = profile_lineage.get(key, [])
            if not isinstance(supplied, list) or len(supplied) > len(expected):
                raise ValueError("Invalid profile lineage")
            for validated, recorded in zip(expected, supplied):
                for field_name, item in recorded.items():
                    if field_name not in validated:
                        raise ValueError("Invalid profile lineage")
                    field = {field_name: validated[field_name]}
                    _restore_lineage(field, {field_name: item})
                    validated[field_name] = field[field_name]

    paths = {}
    for name, events in plans.items():
        timeline, precedence = run(events, 0)
        path = {"events": events, "event_precedence": precedence,
                "event_precedence_note": "Events apply in start-month and input order, then retirement. Period end months are exclusive. Income changes precede the bonus adjustment.",
                "timeline": timeline, "metrics": _metrics(timeline, resolved["goals"])}
        if bands:
            path["band"] = {}
            for label, factor in (("pessimistic", -1), ("optimistic", +1)):
                rows, _ = run(events, factor, trace=False)
                path["band"][label] = {
                    "cash_chf": [row["cash_chf"] for row in rows],
                    "net_worth_chf": [row["net_worth_chf"] for row in rows],
                    "first_negative_month": next((row["month"] for row in rows[1:] if row["cash_chf"] < 0), None),
                }
        drivers = []
        candidates = [("profile", None, key, item, PROFILE_FIELDS[key])
                      for key, item in resolved["fields"].items()]
        for index, event in enumerate(events):
            candidates.extend(("event", index, key, item, EVENT_TYPES[event["type"]]["params"][key])
                              for key, item in event["params"].items() if key != "detailed_housing")
        loan_specs = {"principal_chf": _field("Opening loan balance", "CHF", 0, 1e9),
                      "annual_interest_rate": _field("Loan annual interest rate", "share", 0, 0.5),
                      "monthly_payment_chf": _field("Loan monthly payment", "CHF", 0, 1e7),
                      "term_months": _field("Loan term", "months", 1, 1200, integer=True)}
        for index, debt in enumerate(resolved["debts"]):
            keys = ("principal_chf", "annual_interest_rate", "term_months" if "term_months" in debt else "monthly_payment_chf")
            for key in keys:
                item = {**debt_lineage[index][key], "label": loan_specs[key]["label"]}
                candidates.append(("debt", index, key, item, loan_specs[key]))
        goal_spec = _field("Goal target", "CHF", 0, 1e9)
        for index, goal in enumerate(resolved["goals"]):
            item = {**goal_lineage[index]["target_chf"], "label": goal_spec["label"]}
            candidates.append(("goal", index, "target_chf", item, goal_spec))
        inflation_spec = _field("Annual inflation", "share", -0.999999, 10)
        candidates.append(("inflation", None, "annual_inflation",
                           {"value": annual_inflation, "source": inflation_source,
                            "label": inflation_spec["label"], "unit": "share"}, inflation_spec))
        for year, value in inflation_by_year.items():
            candidates.append(("inflation", year, "inflation_by_year",
                               {"value": value, "source": "user", "label": f"Inflation in {year}",
                                "unit": "share"}, inflation_spec))
        for scope, index, key, item, spec in candidates:
            perturbed = _perturb(item["value"], spec)
            if perturbed is None:
                continue
            changed_profile = deepcopy(resolved) if scope in ("profile", "debt", "goal") else resolved
            changed_events = deepcopy(events) if scope == "event" else events
            changed_inflation = annual_inflation
            changed_overrides = inflation_by_year
            if scope == "profile":
                changed_profile["fields"][key]["value"] = perturbed
            elif scope == "event":
                changed_events[index]["params"][key]["value"] = perturbed
            elif scope == "debt":
                debt = changed_profile["debts"][index]
                debt[key] = perturbed
                if "term_months" in debt:
                    debt["monthly_payment_chf"] = fixed_payment(debt["principal_chf"], debt["annual_interest_rate"], debt["term_months"])
            elif scope == "goal":
                changed_profile["goals"][index][key] = perturbed
            elif index is None:
                changed_inflation = perturbed
            else:
                changed_overrides = {**inflation_by_year, index: perturbed}
            changed_rows, _ = run(changed_events, 0, run_profile=changed_profile, trace=False,
                                  run_inflation=changed_inflation, run_inflation_by_year=changed_overrides)
            changed_metrics = _metrics(changed_rows, changed_profile["goals"])
            original_metrics = path["metrics"]
            goal_impacts = []
            for original_goal, changed_goal in zip(original_metrics["goals"], changed_metrics["goals"]):
                base_cash = original_goal["cash_at_target_chf"]
                new_cash = changed_goal["cash_at_target_chf"]
                base_gap = original_goal["deadline_gap_chf"]
                new_gap = changed_goal["deadline_gap_chf"]
                goal_impacts.append({"name": original_goal["name"], "target_month": original_goal["target_month"],
                    "base_target_chf": original_goal["target_chf"], "perturbed_target_chf": changed_goal["target_chf"],
                    "base_met": original_goal["met"], "perturbed_met": changed_goal["met"],
                    "base_cash_at_target_chf": base_cash, "perturbed_cash_at_target_chf": new_cash,
                    "cash_at_target_delta_chf": round(new_cash - base_cash, 2) if base_cash is not None and new_cash is not None else None,
                    "base_deadline_gap_chf": base_gap, "perturbed_deadline_gap_chf": new_gap,
                    "deadline_gap_delta_chf": round(new_gap - base_gap, 2) if base_gap is not None and new_gap is not None else None,
                    "base_first_attainment_month": original_goal["first_attainment_month"],
                    "perturbed_first_attainment_month": changed_goal["first_attainment_month"]})
            drivers.append({"scope": scope, "event_index": index if scope == "event" else None,
                "event_label": events[index]["label"] if scope == "event" else None,
                "debt_index": index if scope == "debt" else None,
                "debt_name": resolved["debts"][index]["name"] if scope == "debt" else None,
                "goal_index": index if scope == "goal" else None,
                "goal_name": resolved["goals"][index]["name"] if scope == "goal" else None,
                "inflation_year": index if scope == "inflation" else None,
                "field": key, "label": item["label"], "unit": item["unit"], "source": item["source"],
                "category": "user_input" if item["source"] == "user" else "assumption",
                "base_value": item["value"], "perturbed_value": perturbed,
                "perturbation": perturbed - item["value"],
                "terminal_cash_delta_chf": round(changed_metrics["closing_cash_chf"] - original_metrics["closing_cash_chf"], 2),
                "real_terminal_cash_delta_chf": round(changed_metrics["closing_real_cash_chf"] - original_metrics["closing_real_cash_chf"], 2),
                "minimum_liquidity_delta_chf": round(changed_metrics["min_cash_chf"] - original_metrics["min_cash_chf"], 2),
                "goal_impacts": goal_impacts})
        path["sensitivity"] = drivers
        path["sensitivity_note"] = "Each row changes one input by 15%, or a nonzero step at zero, within allowed bounds. User-input rows are conditional alternatives."
        paths[name] = path
    base = paths[BASELINE]["timeline"]
    inflation_coverage = {"cpi_base_month": _add_months(origin, 0), "cpi_base_value": 100.0,
        "recurring_costs": "nominal fixed unless changed by an event",
        "fixed_contracts": "nominal fixed", "one_offs": "nominal fixed",
        "purchasing_power": "nominal balances divided by accumulated CPI"}
    record = {"version": 1, "start": _add_months(origin, 0), "horizon_years": horizon_years,
        "bands": bands, "profile": deepcopy(resolved),
        "plans": {name: deepcopy(events) for name, events in plans.items() if name != BASELINE},
        "inflation": {"annual_inflation": annual_inflation, "inflation_by_year": deepcopy(inflation_by_year),
                      "source": inflation_source, "as_of_date": None, "unit": "share", "frequency": "annual", "transformation": "effective annual to monthly CPI"},
        "debt_lineage": debt_lineage, "goal_lineage": goal_lineage, "inflation_coverage": inflation_coverage}
    comparisons = {name: _compare(base, path["timeline"]) for name, path in paths.items() if name != BASELINE}
    assumptions = _assumptions(resolved, paths)
    from .report import build_comparison_bundle
    report_paths = {name: {**path, "commitments": {"fork_month": 0, "inherited": {
                    "profile": resolved["fields"], "debts": resolved["debts"], "goals": resolved["goals"]},
                    "added": path["events"], "replaced": [], "event_precedence": path["event_precedence"]},
                    "provenance": {"profile": resolved["fields"],
                    "events": [event["params"] for event in path["events"]],
                    "debts": debt_lineage, "goals": goal_lineage, "inflation": record["inflation"]}}
                    for name, path in paths.items()}
    bundle = build_comparison_bundle(
        report_paths, baseline_path=BASELINE, scenario_record=record, assumptions=assumptions,
        comparisons=comparisons, limitations=[
            "Results are conditional on the supplied inputs and resolved assumptions.",
            "Cash shortfalls do not automatically arrange financing or include its cost.",
            "Recurring costs and one-off amounts remain nominal unless events change them.",
            "Goals use cash at the deadline and priority-ordered reservations.",
            "Sensitivity changes one input at a time and does not assign probabilities.",
        ])
    return {
        "start": _add_months(origin, 0),
        "horizon_months": horizon,
        "profile": resolved,
        "scenario_record": record,
        "inflation_coverage": inflation_coverage,
        "provenance_note": "Source dates are included when known; null means the date is unknown.",
        "paths": paths,
        "comparisons": comparisons,
        "assumptions": assumptions,
        "comparison_bundle": bundle,
        "band_note": f"Bands vary dataset defaults and assumptions by ±{BAND:.0%}; values you entered stay fixed.",
        "data_note": DEFAULTS["data_note"],
        "warnings": warnings,
    }


def schema():
    """Field and event definitions for building input forms."""
    def describe(spec, default):
        return {key: spec[key] for key in ("label", "unit", "min", "max", "source", "required", "note", "choices")
                if spec[key] is not None and spec[key] != "" and spec[key] is not False} | {"default": default}
    group = DEFAULTS["by_employment_status"]
    return {
        "profile": {key: describe(spec, None if spec["required"] else spec["default"](group["employed"]))
                    for key, spec in PROFILE_FIELDS.items()},
        "defaults_by_employment_status": group,
        "events": {kind: {"label": spec["label"],
                          "params": {key: describe(p, p["default"](None)) for key, p in spec["params"].items()}}
                   for kind, spec in EVENT_TYPES.items()},
        "horizon_years": {"min": 1, "max": 50, "default": 10},
        "data_note": DEFAULTS["data_note"],
    }
