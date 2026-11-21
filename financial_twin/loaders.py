import csv
import json
from pathlib import Path


def _rows(path):
    with Path(path).open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def _record(value, source, as_of_date, unit, frequency, transformation="identity", **details):
    return {"value": value, "source": source, "as_of_date": as_of_date, "unit": unit,
            "frequency": frequency, "transformation": transformation, **details}


def alexia_tuition_sources(data_dir, config_path):
    from .comparison import alexia_tuition_sources as sources
    return sources(data_dir, config_path)


def load_synthetic_profile(data_dir, profile_id, *, monthly_expenses_chf):
    data_dir = Path(data_dir)
    profiles = [row for row in _rows(data_dir / "synthetic-profiles.csv")
                if row["profile_id"] == profile_id]
    if len(profiles) != 1:
        raise ValueError(f"Expected one profile for {profile_id!r}, found {len(profiles)}")
    profile = profiles[0]
    if profile["currency"] != "CHF":
        raise ValueError("Synthetic profile currency must be CHF")
    assets = []
    for row in _rows(data_dir / "synthetic-assets.csv"):
        if row["profile_id"] != profile_id or row["asset_type"] == "cash":
            continue
        if row["currency"] != "CHF" or row["as_of_date"] != profile["as_of_date"]:
            raise ValueError("Asset currency or snapshot date does not match profile")
        assets.append({
            "id": row["asset_id"],
            "type": row["asset_type"],
            "value_chf": float(row["value_chf"]),
            "annual_return": 0,
        })
    debts = []
    for row in _rows(data_dir / "synthetic-liabilities.csv"):
        if row["profile_id"] != profile_id:
            continue
        if row["currency"] != "CHF" or row["as_of_date"] != profile["as_of_date"]:
            raise ValueError("Liability currency or snapshot date does not match profile")
        debts.append({
            "id": row["liability_id"],
            "principal_chf": float(row["opening_principal_chf"]),
            "annual_interest_rate": float(row["annual_interest_rate"]),
            "monthly_payment_chf": float(row["monthly_payment_chf"]),
        })
    goals_path = data_dir / "synthetic-goals.csv"
    goal_rows = [row for row in _rows(goals_path) if row["profile_id"] == profile_id] if goals_path.exists() else []
    goals = []
    for row in goal_rows:
        if row["currency"] != "CHF":
            raise ValueError("Goal currency does not match profile")
        goals.append({"name": row["goal_name"], "target_chf": float(row["target_amount_chf"]),
                      "date": row["target_date"], "priority": row["priority"]})
    result = {
        "start_date": profile["as_of_date"],
        "currency": "CHF",
        "opening_cash_chf": float(profile["opening_cash_chf"]),
        "assets": assets,
        "debts": debts,
        "phases": [{
            "start_month": 0,
            "end_month": None,
            "monthly_income_chf": float(profile["reference_monthly_net_income_chf"]),
            "monthly_expenses_chf": monthly_expenses_chf,
        }],
        "one_off_costs": [],
        "goals": goals,
    }
    provenance = {}
    def add(path, value, file, row, column, unit, frequency="once"):
        provenance[path] = _record(value, "dataset", profile["as_of_date"], unit, frequency,
                                   "parse numeric value" if isinstance(value, float) else "identity",
                                   file=str(data_dir / file), row=row, column=column)
    add("start_date", result["start_date"], "synthetic-profiles.csv", profile_id, "as_of_date", "date")
    add("currency", result["currency"], "synthetic-profiles.csv", profile_id, "currency", None)
    add("opening_cash_chf", result["opening_cash_chf"], "synthetic-profiles.csv", profile_id, "opening_cash_chf", "CHF")
    add("phases[0].monthly_income_chf", result["phases"][0]["monthly_income_chf"], "synthetic-profiles.csv", profile_id, "reference_monthly_net_income_chf", "CHF", "monthly")
    for key in ("start_month", "end_month"):
        provenance[f"phases[0].{key}"] = _record(result["phases"][0][key], "assumption", None,
                                                  "months", "once", "default phase boundary")
    for index, asset in enumerate(assets):
        for key, column, unit in (("id", "asset_id", None), ("type", "asset_type", None), ("value_chf", "value_chf", "CHF")):
            add(f"assets[{index}].{key}", asset[key], "synthetic-assets.csv", asset["id"], column, unit)
        provenance[f"assets[{index}].annual_return"] = _record(asset["annual_return"], "assumption", None,
                                                                 "share", "annual", "default zero return")
    for index, debt in enumerate(debts):
        for key, column, unit, frequency in (("id", "liability_id", None, "once"), ("principal_chf", "opening_principal_chf", "CHF", "once"), ("annual_interest_rate", "annual_interest_rate", "share", "annual"), ("monthly_payment_chf", "monthly_payment_chf", "CHF", "monthly")):
            add(f"debts[{index}].{key}", debt[key], "synthetic-liabilities.csv", debt["id"], column, unit, frequency)
    from datetime import date
    start = date.fromisoformat(profile["as_of_date"])
    for index, (goal, row) in enumerate(zip(goals, goal_rows)):
        target = date.fromisoformat(goal["date"])
        month = (target.year - start.year) * 12 + target.month - start.month
        normalized_date = date(start.year + (start.month - 1 + month) // 12,
                               (start.month - 1 + month) % 12 + 1, start.day).isoformat()
        for key, value, column, unit, transform in (("name", goal["name"], "goal_name", None, "identity"),
                ("target_chf", goal["target_chf"], "target_amount_chf", "CHF", "parse numeric value"),
                ("target_month", month, "target_date", "months", "month offset from snapshot"),
                ("target_date", normalized_date, "target_date", None, "convert to projection month"),
                ("priority", goal["priority"], "priority", None, "identity")):
            provenance[f"goals[{index}].{key}"] = _record(value, "dataset", None, unit, "once", transform,
                file=str(goals_path), row=row["goal_id"], column=column)
    result["provenance"] = provenance
    return result


def load_starter_scenario(profile_path, config_path, scenario_name, *, start_date):
    with Path(profile_path).open(encoding="utf-8") as source:
        profile = json.load(source)
    with Path(config_path).open(encoding="utf-8") as source:
        config = json.load(source)
    scenarios = [scenario for scenario in config["scenarios"]
                 if scenario["name"] == scenario_name]
    if len(scenarios) != 1:
        raise ValueError(f"Expected one scenario named {scenario_name!r}, found {len(scenarios)}")
    scenario = scenarios[0]
    result = {
        "start_date": start_date,
        "currency": profile["currency"],
        "opening_cash_chf": profile["opening_cash_chf"],
        "assets": [],
        "debts": [],
        "phases": scenario["phases"],
        "one_off_costs": scenario["one_off_costs"],
    }
    provenance = {}
    for key, unit in (("currency", None), ("opening_cash_chf", "CHF")):
        provenance[key] = _record(result[key], "dataset", None, unit, "once", file=str(profile_path), row=key)
    for collection in ("phases", "one_off_costs"):
        for index, item in enumerate(result[collection]):
            for key, value in item.items():
                unit = "CHF" if key.endswith("_chf") else "months" if "month" in key else None
                frequency = "monthly" if key.startswith("monthly_") else "once"
                path = f"{collection}[{index}].{key}"
                provenance[path] = _record(value, "dataset", None, unit, frequency,
                                           file=str(config_path), row=f"{scenario_name}.{path}")
    result["provenance"] = provenance
    return result
