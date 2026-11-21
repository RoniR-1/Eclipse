from copy import deepcopy
import json
from pathlib import Path
from statistics import median

from .engine import create_root, simulate
from .loaders import _record, _rows, load_starter_scenario
from .report import build_comparison_bundle
from .projection import _compare


ROOT = Path(__file__).resolve().parent.parent
CHALLENGE = ROOT / "fixtures" / "demo"
START = "2025-01-01"
HORIZON = 120
PATHS = ("Start working", "Master's degree", "Move abroad")



def alexia_tuition_sources(data_dir, config_path):
    assumptions_path = Path(data_dir) / "scenario-assumptions.csv"
    rows = [row for row in _rows(assumptions_path)
            if row["scenario_id"] == "S001" and row["parameter"] == "annual_tuition_chf"]
    selected = [row for row in rows if row["profile_id"] == "P00001"]
    if len(selected) != 1 or not rows:
        raise ValueError("Expected Alexia's S001 tuition and cohort assumptions")
    with Path(config_path).open(encoding="utf-8") as source:
        config = json.load(source)
    scenarios = [item for item in config["scenarios"] if item["name"] == "Master's degree"]
    if len(scenarios) != 1:
        raise ValueError("Expected one starter master's scenario")
    costs = scenarios[0]["one_off_costs"]
    return {
        "starter_charges": [_record(cost["amount_chf"], "dataset", None, "CHF", "once",
                                    file=str(config_path), row=f"Master's degree.one_off_costs[{index}]")
                            for index, cost in enumerate(costs)],
        "cohort_median": _record(median(float(row["value"]) for row in rows), "dataset", None, "CHF", "annual",
                                 "median of S001 annual tuition rows", file=str(assumptions_path), row="S001 annual_tuition_chf"),
        "profile_specific": _record(float(selected[0]["value"]), "dataset", None, "CHF", "annual",
                                    "parse numeric value", file=str(assumptions_path), row=selected[0]["assumption_id"]),
    }


def _run(scenario):
    root = create_root(scenario)
    nodes = [root, *simulate(root, HORIZON)]
    final = nodes[-1]["state"]
    funding = final["funding_feasibility"]
    timeline = [{"month": node["month"], "date": node["date"],
                 **{key: round(value, 2) for key, value in node["state"]["cash_flow"].items() if isinstance(value, (int, float)) and not isinstance(value, bool)},
                 "cash_chf": round(node["state"]["cash_chf"], 2),
                 "total_assets_chf": round(node["state"]["total_assets_chf"], 2),
                 "debt_chf": round(node["state"]["total_debt_chf"], 2),
                 "real_cash_chf": round(node["state"]["real_cash_chf"], 2),
                 "net_worth_chf": round(node["state"]["net_worth_chf"], 2),
                 "real_net_worth_chf": round(node["state"]["real_net_worth_chf"], 2),
                 "low_point_chf": round(node["state"]["funding_feasibility"]["low_point_chf"], 2),
                 "cpi": node["state"]["cpi"]} for node in nodes]
    lowest = min(timeline, key=lambda item: item["low_point_chf"])
    return {"scenario_record": root["scenario"], "timeline": timeline,
            "events": {"phases": deepcopy(root["scenario"]["phases"]),
                       "one_off_costs": deepcopy(root["scenario"]["one_off_costs"])},
            "metrics": {"terminal_cash_chf": round(final["cash_chf"], 2),
                        "terminal_net_worth_chf": round(final["net_worth_chf"], 2),
                        "real_terminal_cash_chf": round(final["real_cash_chf"], 2),
                        "real_terminal_net_worth_chf": round(final["real_net_worth_chf"], 2),
                        "minimum_liquidity_chf": lowest["low_point_chf"],
                        "minimum_liquidity_month": lowest["month"], "minimum_liquidity_date": lowest["date"],
                        "maximum_funding_need_chf": round(funding["maximum_funding_need_chf"], 2),
                        "first_funding_gap_month": funding["first_funding_gap_month"],
                        "first_funding_gap_date": funding["first_funding_gap_date"],
                        "first_funding_gap_period_start": funding["first_funding_gap_period_start"],
                        "goals": deepcopy(final.get("goals", []))}}


def alexia_comparison():
    profile_path = CHALLENGE / "starter-kit/sample_profile.json"
    config_path = CHALLENGE / "starter-kit/scenario_config.json"
    goal = {"name": "Illustrative cash goal", "target_chf": 10000, "in_months": 24, "priority": "medium"}
    paths = {}
    for name in PATHS:
        scenario = load_starter_scenario(profile_path, config_path, name, start_date=START)
        scenario["goals"] = [deepcopy(goal)]
        scenario.setdefault("provenance", {})
        normalized_goal = {"name": goal["name"], "target_chf": goal["target_chf"], "target_month": goal["in_months"],
                           "target_date": "2027-01-01", "priority": goal["priority"]}
        for key, value in normalized_goal.items():
            scenario["provenance"][f"goals[0].{key}"] = {
                "value": value, "source": "assumption", "as_of_date": None,
                "unit": "CHF" if key == "target_chf" else "months" if key == "target_month" else None,
                "frequency": "once", "transformation": "illustrative goal assumption"}
        paths[name] = _run(scenario)
    baseline = paths["Start working"]["metrics"]
    comparisons = {name: {**_compare(paths["Start working"]["timeline"], path["timeline"]),
                          "terminal_cash_difference_chf": round(path["metrics"]["terminal_cash_chf"] - baseline["terminal_cash_chf"], 2),
                          "minimum_liquidity_difference_chf": round(path["metrics"]["minimum_liquidity_chf"] - baseline["minimum_liquidity_chf"], 2)}
                   for name, path in paths.items() if name != "Start working"}
    master = paths["Master's degree"]
    perturbed = deepcopy(master["scenario_record"])
    for cost in perturbed["one_off_costs"]:
        cost["amount_chf"] *= 1.15
    changed = _run(perturbed)["metrics"]
    original = master["metrics"]
    original_goal, changed_goal = original["goals"][0], changed["goals"][0]
    sensitivity = [{"path": "Master's degree", "parameter": "annual tuition charge", "unit": "CHF per charge",
                    "source": "starter-kit/scenario_config.json", "baseline_value": master["scenario_record"]["one_off_costs"][0]["amount_chf"],
                    "perturbed_value": perturbed["one_off_costs"][0]["amount_chf"], "perturbation": "+15% to each of the two annual charges",
                    "terminal_cash_impact_chf": round(changed["terminal_cash_chf"] - original["terminal_cash_chf"], 2),
                    "minimum_liquidity_impact_chf": round(changed["minimum_liquidity_chf"] - original["minimum_liquidity_chf"], 2),
                    "goal_outcomes": [{"name": original_goal["name"], "baseline_met": original_goal["met"],
                                       "perturbed_met": changed_goal["met"],
                                       "baseline_deadline_gap_chf": original_goal["deadline_gap_chf"],
                                       "perturbed_deadline_gap_chf": changed_goal["deadline_gap_chf"],
                                       "deadline_gap_impact_chf": round(changed_goal["deadline_gap_chf"] - original_goal["deadline_gap_chf"], 2)}]}]
    for name, path in paths.items():
        original_metrics = path["metrics"]
        original_goal = original_metrics["goals"][0]
        for index, phase in enumerate(path["scenario_record"]["phases"]):
            for key, label in (("monthly_income_chf", "monthly income"), ("monthly_expenses_chf", "monthly expenses")):
                altered = deepcopy(path["scenario_record"])
                altered["phases"][index][key] *= 1.15
                changed_metrics = _run(altered)["metrics"]
                changed_goal = changed_metrics["goals"][0]
                sensitivity.append({"path": name, "parameter": f"{label}, phase {index + 1}", "unit": "CHF per month",
                                    "source": "starter-kit/scenario_config.json", "baseline_value": phase[key],
                                    "perturbed_value": altered["phases"][index][key],
                                    "perturbation": f"+15% in phase {index + 1} (month {phase['start_month']} onward)",
                                    "terminal_cash_impact_chf": round(changed_metrics["terminal_cash_chf"] - original_metrics["terminal_cash_chf"], 2),
                                    "minimum_liquidity_impact_chf": round(changed_metrics["minimum_liquidity_chf"] - original_metrics["minimum_liquidity_chf"], 2),
                                    "goal_outcomes": [{"name": original_goal["name"], "baseline_met": original_goal["met"],
                                                       "perturbed_met": changed_goal["met"],
                                                       "baseline_deadline_gap_chf": original_goal["deadline_gap_chf"],
                                                       "perturbed_deadline_gap_chf": changed_goal["deadline_gap_chf"],
                                                       "deadline_gap_impact_chf": round(changed_goal["deadline_gap_chf"] - original_goal["deadline_gap_chf"], 2)}]})
    sources = alexia_tuition_sources(CHALLENGE / "Data", config_path)
    expected = {"Start working": 152000, "Master's degree": 144600, "Move abroad": 97500}
    checks = [{"name": f"{name} terminal cash", "expected_chf": amount,
               "actual_chf": paths[name]["metrics"]["terminal_cash_chf"],
               "passed": paths[name]["metrics"]["terminal_cash_chf"] == amount}
              for name, amount in expected.items()]
    checks.append({"name": "Master's maximum funding need", "expected_chf": 18600,
                   "actual_chf": original["maximum_funding_need_chf"],
                   "passed": original["maximum_funding_need_chf"] == 18600})
    checks.append({"name": "Higher tuition cannot improve terminal cash", "passed": sensitivity[0]["terminal_cash_impact_chf"] <= 0})
    with profile_path.open(encoding="utf-8") as source:
        profile = json.load(source)
    result = {"name": "Alexia starter comparison", "start_date": START, "horizon_months": HORIZON,
            "currency": "CHF", "jurisdiction": "Switzerland", "baseline_path": "Start working",
            "profile": {"name": profile["name"], "fictional": profile["fictional"],
                        "age_at_start": profile["age_at_start"], "city": profile["city"], "country": profile["country"],
                        "opening_cash_chf": profile["opening_cash_chf"]},
            "illustrative_goal": {**goal, "source": "assumption", "note": "Added for goal illustration; absent from the starter profile."},
            "assumptions": {"annual_inflation": 0, "inflation_source": "assumption", "base_cpi": 100,
                            "tuition_sources": sources},
            "paths": paths, "comparisons": comparisons, "sensitivity": sensitivity, "sanity_checks": checks,
            "limitations": ["Synthetic illustrative scenarios are conditional on the starter assumptions and are not forecasts.",
                            "Cash shortfalls are shown without automatically arranging financing or charging financing costs.",
                            "Inflation is assumed to be zero, so real and nominal amounts coincide.",
                            "Moving abroad is modelled in CHF without exchange rates, country-specific taxes or relocation jurisdiction changes.",
                            "The illustrative goal uses cash at its deadline and the model's priority reservation policy."]}

    report_paths = {}
    for name, path in paths.items():
        metrics = path["metrics"]
        normalized_metrics = {**metrics, "closing_cash_chf": metrics["terminal_cash_chf"],
            "closing_net_worth_chf": metrics["terminal_net_worth_chf"],
            "closing_real_cash_chf": metrics["real_terminal_cash_chf"],
            "closing_real_net_worth_chf": metrics["real_terminal_net_worth_chf"],
            "min_cash_chf": metrics["minimum_liquidity_chf"],
            "min_cash_date": metrics["minimum_liquidity_date"]}
        drivers = []
        for item in sensitivity:
            if item["path"] != name:
                continue
            drivers.append({"field": item["parameter"], "label": item["parameter"],
                "unit": item["unit"], "source": item["source"], "base_value": item["baseline_value"],
                "perturbed_value": item["perturbed_value"], "perturbation": item["perturbation"],
                "terminal_cash_delta_chf": item["terminal_cash_impact_chf"],
                "minimum_liquidity_delta_chf": item["minimum_liquidity_impact_chf"],
                "goal_impacts": [{**goal, "base_met": goal["baseline_met"],
                    "deadline_gap_delta_chf": goal["deadline_gap_impact_chf"]} for goal in item["goal_outcomes"]]})
        report_paths[name] = {**path, "metrics": normalized_metrics, "sensitivity": drivers,
            "provenance": path["scenario_record"]["provenance"],
            "commitments": {"fork_month": 0, "inherited": {"goals": path["scenario_record"]["goals"]},
                            "added": path["events"], "replaced": []}}
    result["comparison_bundle"] = build_comparison_bundle(
        report_paths, baseline_path=result["baseline_path"],
        scenario_record={name: path["scenario_record"] for name, path in paths.items()},
        assumptions=result["assumptions"], comparisons=comparisons, limitations=result["limitations"])

    def portable(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "file" and isinstance(item, str) and Path(item).is_absolute():
                    value[key] = str(Path(item).relative_to(ROOT))
                else:
                    portable(item)
        elif isinstance(value, list):
            for item in value:
                portable(item)

    portable(result)
    return result
