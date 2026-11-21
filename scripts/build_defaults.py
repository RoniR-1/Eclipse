"""Derive dataset defaults for the projection engine from the LauzHack synthetic CSVs.

Run once after the challenge data is available:

    python scripts/build_defaults.py [path/to/Data]

Writes financial_twin/defaults.json. The app reads only that small file at runtime,
so the 552k-row transaction CSV is never parsed per request.

The synthetic data shows almost no relationship between profile attributes and
spending (see README), so defaults are simple medians/means grouped by employment
status, not a fitted model.
"""
from collections import defaultdict
import csv
import json
import os
from pathlib import Path
from statistics import mean, median
import sys

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "financial_twin" / "defaults.json"
CANDIDATES = [
    ROOT / "ai-financial-twin-lauzhack-2026-main" / "Data",
    ROOT / "ai-financial-twin-lauzhack-2026" / "Data",
    ROOT.parent / "ai-financial-twin-lauzhack-2026-main" / "Data",
    ROOT.parent / "ai-financial-twin-lauzhack-2026" / "Data",
]

FIXED = {"health_insurance", "utilities", "transport", "telecom"}
DISCRETIONARY = {"groceries", "restaurants", "retail", "leisure", "pharmacy", "travel"}
MONTHS = 24  # the ledger covers 2025-01 .. 2026-12


def find_data_dir(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    options = [Path(argv[0])] if argv else []
    if os.environ.get("TWIN_DATA_DIR"):
        options.append(Path(os.environ["TWIN_DATA_DIR"]))
    for path in options + CANDIDATES:
        if (path / "synthetic-profiles.csv").exists():
            return path
    raise SystemExit("Challenge Data directory not found; pass it as the first argument.")


def transactions_path(data_dir):
    for path in (data_dir / "synthetic-transactions" / "synthetic-transactions.csv",
                 data_dir / "synthetic-transactions.csv"):
        if path.exists():
            return path
    raise SystemExit("synthetic-transactions.csv not found (unzip synthetic-transactions.zip).")


def rows(path):
    with Path(path).open(newline="", encoding="utf-8") as source:
        yield from csv.DictReader(source)


def build(data_dir):
    profiles = {row["profile_id"]: row for row in rows(data_dir / "synthetic-profiles.csv")}
    totals = defaultdict(lambda: defaultdict(float))
    category_totals = defaultdict(lambda: defaultdict(float))
    unexpected_amounts = []
    for row in rows(transactions_path(data_dir)):
        amount = float(row["amount_chf"])
        category = row["category"]
        totals[row["profile_id"]][category] += amount
        category_totals[row["profile_id"]][category] += -amount
        if category == "unexpected_expense":
            unexpected_amounts.append(-amount)

    groups = defaultdict(list)
    for profile_id, profile in profiles.items():
        groups[profile["employment_status"]].append(profile_id)
        groups["all"].append(profile_id)

    def stats(ids):
        fixed = [-sum(totals[i][c] for c in FIXED) / MONTHS for i in ids]
        disc = [-sum(totals[i][c] for c in DISCRETIONARY) / MONTHS for i in ids]
        irregular = [-totals[i]["unexpected_expense"] / MONTHS for i in ids]
        bonus = [totals[i]["annual_bonus"] / totals[i]["salary_or_support"]
                 for i in ids if totals[i]["salary_or_support"] > 0]
        return {
            "profiles": len(ids),
            "other_fixed_costs_chf": round(median(fixed), 2),
            "discretionary_chf": round(median(disc), 2),
            "irregular_costs_chf": round(mean(irregular), 2),
            "bonus_share_of_income": round(mean(bonus), 4) if bonus else 0.0,
        }

    tuition, duration = [], []
    for row in rows(data_dir / "scenario-assumptions.csv"):
        if row["scenario_id"] == "S001" and row["parameter"] == "annual_tuition_chf":
            tuition.append(float(row["value"]))
        if row["scenario_id"] == "S001" and row["parameter"] == "study_duration_years":
            duration.append(float(row["value"]))

    breakdown_categories = sorted(FIXED | DISCRETIONARY)
    return {
        "source": "LauzHack 2026 synthetic data: 1,200 profiles, 24-month transaction ledger",
        "data_note": ("Spending in the synthetic data is nearly independent of income and household; "
                      "values are dataset medians (costs) and means (irregular costs, bonus share)."),
        "by_employment_status": {name: stats(ids) for name, ids in sorted(groups.items())},
        "category_medians_chf": {
            category: round(median(category_totals[i][category] / MONTHS for i in profiles), 2)
            for category in breakdown_categories
        },
        "median_rent_chf": round(median(float(p["reference_monthly_rent_chf"]) for p in profiles.values()), 2),
        "unexpected_event_amount_chf": round(median(unexpected_amounts), 2),
        "unexpected_events_per_year": round(len(unexpected_amounts) / len(profiles) / (MONTHS / 12), 3),
        "education": {
            "study_duration_months": int(round(median(duration) * 12)),
            "annual_tuition_chf": round(median(tuition), 2),
            "profiles_with_assumptions": len(tuition),
        },
    }


def main():
    data_dir = find_data_dir()
    defaults = build(data_dir)
    OUT.write_text(json.dumps(defaults, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)} from {data_dir}")


if __name__ == "__main__":
    main()
