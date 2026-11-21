"""Deterministic projection engine for a user-entered financial profile.

Pipeline:
    raw user input ──resolve_profile──► values tagged user / dataset / assumption
    decisions      ──resolve_event────► typed events with parameters and a start month
    _build()       ──────────────────► monthly arrays (income, costs, one-offs, deposits)
    run_arrays()   ──────────────────► C(t+1) = C(t) + I(t) − E(t) − K(t) − debt payments
    _metrics() / _compare()        ──► liquidity, goals and trade-offs vs. the baseline

Month 0 is the month the projection is requested. Point k of a timeline is the
state after k monthly transitions. Every number is either supplied by the user,
derived from the LauzHack synthetic data (financial_twin/defaults.json), or an
explicit assumption the user can override. Nothing here is a forecast.
"""
from __future__ import annotations

from datetime import date
import json
import math
from pathlib import Path

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
                lambda p: max(round(p["monthly_net_income_chf"] * 1.15), 4800) if p else 4800, direction=+1,
                note="Max of current income +15% and the starter kit's CHF 4,800"),
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
            "moving_cost_chf": _p("Moving cost", "CHF", 0, 1e6, lambda p: 6500, direction=-1,
                                  note="Starter kit 'Move abroad' cost"),
            "rent_change_pct": _p("Rent change", "share", -0.9, 3, lambda p: 0.0),
            "income_change_pct": _p("Income change", "share", -0.9, 3, lambda p: 0.0),
            "cost_of_living_change_pct": _p("Other living costs change", "share", -0.9, 3, lambda p: 0.0),
        },
    },
    "housing_change": {
        "label": "Change housing",
        "params": {
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
            **({"note": spec["note"]} if spec["note"] and source != "user" else {})}


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

    debts = []
    for index, debt in enumerate(raw.get("debts") or []):
        _check_keys(debt, ["name", "principal_chf", "annual_interest_rate", "monthly_payment_chf"], "debt")
        debts.append({
            "name": str(debt.get("name") or f"Debt {index + 1}")[:80],
            "principal_chf": _number(debt.get("principal_chf"), "debt principal_chf", {"min": 0, "max": 1e9}),
            "annual_interest_rate": _number(debt.get("annual_interest_rate", 0), "annual_interest_rate",
                                            {"min": 0, "max": 0.5}),
            "monthly_payment_chf": _number(debt.get("monthly_payment_chf", 0), "monthly_payment_chf",
                                           {"min": 0, "max": 1e7}),
        })
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


def resolve_event(raw, profile, start):
    _check_keys(raw, ["type", "label", "start_month", "in_months", "date", "params"], "event")
    kind = raw.get("type")
    if kind not in EVENT_TYPES:
        raise ValueError(f"event type must be one of {list(EVENT_TYPES)}")
    spec = EVENT_TYPES[kind]
    params_raw = raw.get("params") or {}
    _check_keys(params_raw, spec["params"], f"{kind} params")
    p = _values(profile)
    params = {}
    for key, pspec in spec["params"].items():
        given = params_raw.get(key)
        if given is None:
            params[key] = _value(pspec["default"](p), pspec["source"], pspec)
        else:
            params[key] = _value(_number(given, key, pspec), "user", pspec)
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


def _build(profile, events, horizon, factor=0):
    p = {key: _adjusted(item, PROFILE_FIELDS[key], factor) for key, item in profile["fields"].items()}
    salary = [p["monthly_net_income_chf"]] * horizon
    rent = [p["monthly_rent_chf"]] * horizon
    fixed = [p["other_fixed_costs_chf"]] * horizon
    disc = [p["discretionary_chf"]] * horizon
    irregular = [p["irregular_costs_chf"]] * horizon
    one_off = [0.0] * horizon
    deposit = [0.0] * horizon
    marks = [[] for _ in range(horizon)]

    for event in sorted(events, key=lambda item: item["start_month"]):
        m0 = event["start_month"]
        if m0 >= horizon:
            continue
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
                rent[t] = q["new_rent_chf"]
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

    # Retirement applies in every path, after decisions have shaped the salary.
    retire_at = None
    if p["employment_status"] != "retired" and p["age"] < p["retirement_age"]:
        months = math.ceil((p["retirement_age"] - p["age"]) * 12)
        if months < horizon:
            retire_at = months
            pension = max(0.0, salary[months - 1]) * p["pension_share_of_income"]
            for t in range(months, horizon):
                salary[t] = pension
            marks[months].append(f"Retirement at {p['retirement_age']}")

    bonus = p["bonus_share_of_income"]
    income = [max(0.0, s) * (1 if retire_at is not None and t >= retire_at else 1 + bonus)
              for t, s in enumerate(salary)]
    expenses = [max(0.0, rent[t] + fixed[t] + disc[t] + irregular[t]) for t in range(horizon)]
    return {"income": income, "expenses": expenses, "one_off": one_off, "deposit": deposit,
            "marks": marks, "investments": p["investments_chf"],
            "investment_annual_return": p["investment_annual_return"], "cash": p["cash_chf"]}


# ── core recurrence ────────────────────────────────────────────────────────

def run_arrays(opening_cash, income, expenses, one_off=None, deposit=None, *, investments=0.0,
               investment_annual_return=0.0, debts=(), start=(2025, 1), marks=None):
    """C(t+1) = C(t) + I(t) − E(t) − K(t) − debt payments − deposits moved out of cash."""
    horizon = len(income)
    one_off = one_off or [0.0] * horizon
    deposit = deposit or [0.0] * horizon
    marks = marks or [[] for _ in range(horizon)]
    growth = (1 + investment_annual_return) ** (1 / 12) - 1
    loans = [dict(debt) for debt in debts]
    cash, held, locked = float(opening_cash), float(investments), 0.0

    def point(month, flows, low):
        debt = sum(loan["principal_chf"] for loan in loans)
        upcoming = expenses[min(month, horizon - 1)] if horizon else 0
        return {"month": month, "date": _add_months(start, month),
                **{key: round(value, 2) for key, value in flows.items()},
                "cash_chf": round(cash, 2), "low_point_chf": round(low, 2),
                "investments_chf": round(held, 2), "deposits_chf": round(locked, 2),
                "debt_chf": round(debt, 2), "net_worth_chf": round(cash + held + locked - debt, 2),
                "runway_months": round(max(cash, 0) / upcoming, 1) if upcoming > 0 else None,
                "events": list(marks[month]) if month < horizon else []}

    zero = dict.fromkeys(("income_chf", "expenses_chf", "one_off_chf", "deposit_chf", "debt_payment_chf",
                          "debt_interest_chf", "investment_return_chf", "net_cash_flow_chf"), 0.0)
    rows = [point(0, zero, cash)]
    for t in range(horizon):
        low = cash - one_off[t] - deposit[t]
        interest = payment = 0.0
        for loan in loans:
            accrued = loan["principal_chf"] * loan["annual_interest_rate"] / 12
            paid = min(loan["monthly_payment_chf"], loan["principal_chf"] + accrued)
            loan["principal_chf"] += accrued - paid
            interest += accrued
            payment += paid
        returned = held * growth
        held += returned
        net = income[t] - expenses[t] - one_off[t] - payment
        cash += net - deposit[t]
        locked += deposit[t]
        flows = {"income_chf": income[t], "expenses_chf": expenses[t], "one_off_chf": one_off[t],
                 "deposit_chf": deposit[t], "debt_payment_chf": payment, "debt_interest_chf": interest,
                 "investment_return_chf": returned, "net_cash_flow_chf": net}
        rows.append(point(t + 1, flows, min(low, cash)))
    return rows


# ── metrics ────────────────────────────────────────────────────────────────

def _metrics(rows, goals):
    flows = rows[1:]
    lowest = min(rows, key=lambda row: row["low_point_chf"])
    negative = [row for row in flows if row["cash_chf"] < 0]
    income = sum(row["income_chf"] for row in flows)
    net = sum(row["net_cash_flow_chf"] for row in flows)
    horizon = len(flows)
    committed = 0.0
    results = []
    for goal in sorted(goals, key=lambda g: (PRIORITIES[g["priority"]], g["target_month"])):
        if goal["target_month"] > horizon:
            results.append({**goal, "met": None, "progress": None, "cash_at_target_chf": None,
                            "note": "Target date is beyond the projection horizon"})
            continue
        available = rows[goal["target_month"]]["cash_chf"] - committed
        met = available >= goal["target_chf"]
        if met:
            committed += goal["target_chf"]
        progress = 1.0 if goal["target_chf"] == 0 else max(0.0, min(available / goal["target_chf"], 1.0))
        results.append({**goal, "met": met, "progress": round(progress, 3),
                        "cash_at_target_chf": round(available, 2)})
    end = rows[-1]
    return {
        "closing_cash_chf": end["cash_chf"],
        "closing_net_worth_chf": end["net_worth_chf"],
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
                      "unit": item["unit"], "source": item["source"], "note": item.get("note", "")}
                     for item in event["params"].values()
                     if item["source"] != "user" and item["value"] is not None]
    return used


def project(profile, scenarios=None, *, horizon_years=10, start=None, bands=True):
    """Project a baseline plus up to five named decision paths from user inputs.

    profile    dict of user inputs (see PROFILE_FIELDS); unknown values are filled in.
    scenarios  {name: [event, ...]}; each event is {"type", "start_month"|"in_months"|"date", "params"}.
    """
    horizon_years = _number(horizon_years, "horizon_years", {"integer": True, "min": 1, "max": 50})
    horizon = horizon_years * 12
    origin = _start(start)
    resolved = resolve_profile(profile, origin)
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
        plans[name] = [resolve_event(event, resolved, origin) for event in events]
        warnings += [f"{name}: '{event['label']}' starts after the horizon and is ignored"
                     for event in plans[name] if event["start_month"] >= horizon]

    def run(events, factor):
        built = _build(resolved, events, horizon, factor)
        return run_arrays(built["cash"], built["income"], built["expenses"], built["one_off"],
                          built["deposit"], investments=built["investments"],
                          investment_annual_return=built["investment_annual_return"],
                          debts=resolved["debts"], start=origin, marks=built["marks"])

    paths = {}
    for name, events in plans.items():
        timeline = run(events, 0)
        path = {"events": events, "timeline": timeline, "metrics": _metrics(timeline, resolved["goals"])}
        if bands:
            path["band"] = {}
            for label, factor in (("pessimistic", -1), ("optimistic", +1)):
                rows = run(events, factor)
                path["band"][label] = {
                    "cash_chf": [row["cash_chf"] for row in rows],
                    "net_worth_chf": [row["net_worth_chf"] for row in rows],
                    "first_negative_month": next((row["month"] for row in rows[1:] if row["cash_chf"] < 0), None),
                }
        paths[name] = path
    base = paths[BASELINE]["timeline"]
    return {
        "start": _add_months(origin, 0),
        "horizon_months": horizon,
        "profile": resolved,
        "paths": paths,
        "comparisons": {name: _compare(base, path["timeline"]) for name, path in paths.items() if name != BASELINE},
        "assumptions": _assumptions(resolved, paths),
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
