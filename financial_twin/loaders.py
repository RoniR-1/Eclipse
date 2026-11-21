import csv
import json
from pathlib import Path


def _rows(path):
    with Path(path).open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


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
    return {
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
    }


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
    return {
        "start_date": start_date,
        "currency": profile["currency"],
        "opening_cash_chf": profile["opening_cash_chf"],
        "assets": [],
        "debts": [],
        "phases": scenario["phases"],
        "one_off_costs": scenario["one_off_costs"],
    }
