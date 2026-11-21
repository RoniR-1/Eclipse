import csv
import os
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

from .projection import _frequency, _month_key, resolve_profile

DATA = Path(__file__).resolve().parent.parent / "fixtures" / "demo" / "Data"


@lru_cache(maxsize=1)
def _data():
    tables = {}
    data_root = Path(os.environ.get("FINANCIAL_TWIN_DATA_ROOT", "").strip() or DATA.parent) / "Data"
    for name in ("profiles", "assets", "liabilities", "goals"):
        path = data_root / f"synthetic-{name}.csv"
        with path.open(newline="", encoding="utf-8") as source:
            groups = {}
            for row in csv.DictReader(source):
                groups.setdefault(row["profile_id"], []).append(row)
        tables[name] = groups
    return tables


def synthetic_projection(profile_id, overrides, start):
    if not isinstance(profile_id, str) or len(profile_id) != 6 or profile_id[0] != "P" or not profile_id[1:].isdigit():
        raise ValueError("Invalid synthetic profile ID.")
    tables = _data()
    rows = tables["profiles"].get(profile_id, [])
    if len(rows) != 1:
        raise ValueError("Synthetic profile was not found.")
    original = rows[0]
    if original["currency"] != "CHF":
        raise ValueError("Synthetic profile currency must be CHF.")
    as_of = original["as_of_date"]
    fields = {
        "name": original["display_name"],
        "age": int(original["age_at_start"]),
        "cash_chf": float(original["opening_cash_chf"]),
        "monthly_net_income_chf": float(original["reference_monthly_net_income_chf"]),
        "monthly_rent_chf": float(original["reference_monthly_rent_chf"]),
        "employment_status": original["employment_status"],
        "household_type": original["household_type"],
    }
    assets = [row for row in tables["assets"].get(profile_id, []) if row["asset_type"] != "cash"]
    debts = tables["liabilities"].get(profile_id, [])
    goals = tables["goals"].get(profile_id, [])
    if any(row["currency"] != "CHF" or row["as_of_date"] != as_of for row in assets + debts):
        raise ValueError("Synthetic snapshot date or currency mismatch.")
    if any(row["currency"] != "CHF" for row in goals):
        raise ValueError("Synthetic goal currency mismatch.")
    fields["investments_chf"] = sum(float(row["value_chf"]) for row in assets)
    fields["debts"] = [{"name": row["liability_id"], "principal_chf": float(row["opening_principal_chf"]),
                        "annual_interest_rate": float(row["annual_interest_rate"]),
                        "monthly_payment_chf": float(row["monthly_payment_chf"])} for row in debts]
    fields["goals"] = [{"name": row["goal_name"], "target_chf": float(row["target_amount_chf"]),
                        "date": row["target_date"][:7], "priority": row["priority"]} for row in goals]
    supplied = {} if overrides is None else overrides
    if not isinstance(supplied, dict):
        raise ValueError("profile must be an object.")
    fields.update(deepcopy(supplied))
    origin = start or as_of[:7]
    resolved = resolve_profile(fields, _month_key(origin, "start"))
    lineage = {"fields": {}, "debts": [], "goals": []}
    profile_columns = {"age": "age_at_start", "cash_chf": "opening_cash_chf",
                       "monthly_net_income_chf": "reference_monthly_net_income_chf",
                       "monthly_rent_chf": "reference_monthly_rent_chf",
                       "employment_status": "employment_status", "household_type": "household_type"}
    def record(value, unit, frequency, file, row, column, transformation="identity", source_date=None):
        return {"value": value, "source": "dataset", "as_of_date": source_date, "unit": unit,
                "frequency": frequency, "transformation": transformation,
                "file": str(Path(os.environ.get("FINANCIAL_TWIN_DATA_ROOT", "").strip() or "fixtures/demo") / "Data" / f"synthetic-{file}.csv"),
                "row": row, "column": column}
    for key, column in profile_columns.items():
        if key not in supplied:
            item = resolved["fields"][key]
            lineage["fields"][key] = {**item, **record(item["value"], item["unit"], item["frequency"], "profiles", profile_id, column, "parse numeric value" if isinstance(item["value"], (int, float)) else "identity", as_of)}
    if "investments_chf" not in supplied:
        item = resolved["fields"]["investments_chf"]
        lineage["fields"]["investments_chf"] = {**item, **record(item["value"], item["unit"], item["frequency"], "assets", profile_id, "value_chf", "sum non-cash investment assets", as_of)}
    if "debts" not in supplied:
        for loan, row in zip(resolved["debts"], debts):
            columns = {"name": "liability_id", "principal_chf": "opening_principal_chf", "annual_interest_rate": "annual_interest_rate", "monthly_payment_chf": "monthly_payment_chf"}
            lineage["debts"].append({key: record(value, "CHF" if key.endswith("_chf") else "share" if key.endswith("_rate") else None, _frequency(key), "liabilities", row["liability_id"], columns[key], "parse numeric value" if isinstance(value, (int, float)) else "identity", as_of) for key, value in loan.items()})
    if "goals" not in supplied:
        for goal, row in zip(resolved["goals"], goals):
            columns = {"name": "goal_name", "target_chf": "target_amount_chf", "target_month": "target_date", "target_date": "target_date", "priority": "priority"}
            lineage["goals"].append({key: record(value, "CHF" if key == "target_chf" else "months" if key == "target_month" else None, _frequency(key), "goals", row["goal_id"], columns[key], "month offset from projection start" if key == "target_month" else "convert to projection month" if key == "target_date" else "identity") for key, value in goal.items()})
    return fields, origin, lineage
