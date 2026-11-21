from copy import deepcopy

from .debt import fixed_payment
from .engine import create_root, simulate
from .report import build_comparison_bundle


def _timeline(nodes):
    rows = []
    for node in nodes:
        state = node["state"]
        flow = state["cash_flow"]
        rows.append({"month": node["month"], "date": node["date"],
                     "cash_chf": state["cash_chf"], "net_worth_chf": state["net_worth_chf"],
                     "real_cash_chf": state["real_cash_chf"], "real_net_worth_chf": state["real_net_worth_chf"],
                     "total_assets_chf": state["total_assets_chf"], "debt_chf": state["total_debt_chf"],
                     "cash_change_after_transfers_chf": flow["cash_change_after_transfers_chf"],
                     "income_chf": flow["income_chf"], "expenses_chf": flow["expenses_chf"],
                     "one_off_costs_chf": flow["one_off_costs_chf"], "debt_payments_chf": flow["debt_payments_chf"],
                     "investment_contribution_chf": flow["investment_contribution_chf"],
                     "borrowing_proceeds_chf": flow.get("borrowing_proceeds_chf", 0),
                     "low_point_chf": state["funding_feasibility"]["low_point_chf"]})
    return rows


def _metrics(nodes):
    state = nodes[-1]["state"]
    funding = state["funding_feasibility"]
    minimum = min(nodes, key=lambda item: item["state"]["funding_feasibility"]["low_point_chf"])
    minimum_cash = funding.get("minimum_cash_chf", minimum["state"]["funding_feasibility"]["low_point_chf"])
    return {"closing_cash_chf": state["cash_chf"], "closing_net_worth_chf": state["net_worth_chf"],
            "closing_real_cash_chf": state["real_cash_chf"], "closing_real_net_worth_chf": state["real_net_worth_chf"],
            "min_cash_chf": minimum_cash,
            "min_cash_month": funding.get("minimum_cash_month", minimum["month"]),
            "min_cash_date": funding.get("minimum_cash_date", minimum["date"]),
            "minimum_liquidity_scope": "full path" if "minimum_cash_chf" in funding else "supplied path",
            "maximum_funding_need_chf": funding["maximum_funding_need_chf"],
            "first_funding_gap_month": funding["first_funding_gap_month"],
            "first_funding_gap_date": funding["first_funding_gap_date"],
            "first_funding_gap_period_start": funding.get("first_funding_gap_period_start"),
            "minimum_reserve_breaches": funding["minimum_reserve_breaches"],
            "budget_breaches": funding["budget_breaches"], "goals": state.get("goals", [])}


def _starting_checkpoint(nodes):
    index = None
    for i in range(1, len(nodes)):
        before, after = nodes[i - 1], nodes[i]
        fork = after["branch_id"] != before["branch_id"] and not (before["branch_id"] == "root" and after["branch_id"] == "main")
        if fork or after["scenario"] != before["scenario"] or after["state"]["active_variables"]["preferences"] != before["state"]["active_variables"]["preferences"]:
            index = i
    if index is None:
        return deepcopy(nodes[0]), 0
    parent = deepcopy(nodes[index - 1])
    first = nodes[index]
    event = first.get("event_state", {})
    if event:
        parent["state"]["assets"] = deepcopy(event["assets"])
        parent["state"]["debts"] = deepcopy(event["debts"])
        parent["state"]["cash_chf"] += event.get("borrowing_proceeds_chf", 0)
    parent["scenario"] = deepcopy(first["scenario"])
    parent["state"]["active_variables"]["preferences"] = deepcopy(first["state"]["active_variables"]["preferences"])
    return parent, index


def engine_comparison_bundle(nodes):
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("Provide a nonempty path.")
    for previous, current in zip(nodes, nodes[1:]):
        if current.get("parent_id") != previous.get("id") or current.get("month") != previous.get("month", -1) + 1:
            raise ValueError("Path nodes must form a continuous chain.")
    checkpoint, index = _starting_checkpoint(nodes)
    scenario = checkpoint["scenario"]
    months = nodes[-1]["month"] - checkpoint["month"]
    if months < 0 or months > 600:
        raise ValueError("A path report sensitivity segment must be between 0 and 600 months.")
    candidates = []
    if index == 0:
        candidates.append(("scenario", None, "opening_cash_chf", scenario["opening_cash_chf"], "CHF"))
    for i, phase in enumerate(scenario["phases"]):
        if phase["end_month"] is None or phase["end_month"] > checkpoint["month"]:
            for key in ("monthly_income_chf", "monthly_expenses_chf"):
                candidates.append(("phases", i, key, phase[key], "CHF"))
    for i, cost in enumerate(scenario["one_off_costs"]):
        if cost["month"] >= checkpoint["month"]:
            candidates.append(("one_off_costs", i, "amount_chf", cost["amount_chf"], "CHF"))
    candidates.append(("scenario", None, "annual_inflation", scenario["annual_inflation"], "share"))
    for year, rate in scenario.get("inflation_by_year", {}).items():
        candidates.append(("inflation_by_year", year, "rate", rate, "share"))
    for i, term in enumerate(scenario.get("expense_terms", [])):
        if term["end_month"] is None or term["end_month"] > checkpoint["month"]:
            candidates.append(("expense_terms", i, "monthly_expenses_chf", term["monthly_expenses_chf"], "CHF"))
    for key, value in checkpoint["state"]["active_variables"]["preferences"].items():
        candidates.append(("preferences", None, key, value, "months" if key == "emergency_months" else "share"))
    for i, asset in enumerate(checkpoint["state"]["assets"]):
        if asset["id"] != "__contributions__":
            for key, unit in (("value_chf", "CHF"), ("annual_return", "share")):
                candidates.append(("assets", i, key, asset[key], unit))
    for i, loan in enumerate(checkpoint["state"]["debts"]):
        for key, unit in (("principal_chf", "CHF"), ("annual_interest_rate", "share")):
            candidates.append(("debts", i, key, loan[key], unit))
        key = "term_months" if "term_months" in loan else "monthly_payment_chf"
        candidates.append(("debts", i, key, loan[key], "months" if key == "term_months" else "CHF"))
    for i, goal in enumerate(scenario.get("goals", [])):
        candidates.append(("goals", i, "target_chf", goal["target_chf"], "CHF"))
    base = _metrics(nodes)
    drivers = []
    replay = [checkpoint, *simulate(checkpoint, months)]
    if index:
        replay = [*nodes[:index], *replay[1:]]
    replay_metrics = _metrics(replay)
    replay_ok = abs(replay_metrics["closing_cash_chf"] - base["closing_cash_chf"]) < 0.01 and abs(replay_metrics["min_cash_chf"] - base["min_cash_chf"]) < 0.01
    for scope, item_index, key, value, unit in candidates:
        if not replay_ok:
            break
        step = abs(value) * 0.15 or (100 if unit == "CHF" else 1 if unit == "months" else 0.01)
        perturbed = value + (max(1, round(step)) if key == "term_months" else step)
        if scope == "preferences" and key in ("savings_rate", "risk_tolerance", "safety_priority") and perturbed > 1:
            perturbed = max(0, value - step)
        updated = deepcopy(checkpoint)
        changed_scenario = updated["scenario"]
        if scope == "scenario":
            changed_scenario[key] = perturbed
            if key == "opening_cash_chf":
                updated = create_root(changed_scenario)
        elif scope == "inflation_by_year":
            changed_scenario["inflation_by_year"][item_index] = perturbed
        elif scope == "preferences":
            updated["state"]["active_variables"]["preferences"][key] = perturbed
        elif scope in ("assets", "debts"):
            changed_item = updated["state"][scope][item_index]
            changed_item[key] = perturbed
            if scope == "debts" and "term_months" in changed_item:
                changed_item["monthly_payment_chf"] = fixed_payment(changed_item["principal_chf"], changed_item["annual_interest_rate"], changed_item["term_months"])
                changed_item["fixed_payment_chf"] = changed_item["monthly_payment_chf"]
        else:
            changed_scenario[scope][item_index][key] = perturbed
        changed_nodes = [updated, *simulate(updated, months)]
        if index:
            changed_nodes = [*nodes[:index], *changed_nodes[1:]]
        changed = _metrics(changed_nodes)
        impacts = []
        for before, after in zip(base["goals"], changed["goals"]):
            old_gap, new_gap = before["deadline_gap_chf"], after["deadline_gap_chf"]
            impacts.append({"name": before["name"], "base_met": before["met"], "perturbed_met": after["met"],
                            "base_deadline_gap_chf": old_gap, "perturbed_deadline_gap_chf": new_gap,
                            "deadline_gap_delta_chf": round(new_gap - old_gap, 2) if old_gap is not None and new_gap is not None else None,
                            "base_first_attainment_month": before["first_attainment_month"],
                            "perturbed_first_attainment_month": after["first_attainment_month"]})
        path = key if scope == "scenario" else f"preferences.{key}" if scope == "preferences" else f"inflation_by_year.{item_index}" if scope == "inflation_by_year" else f"{scope}[{item_index}].{key}"
        source = scenario.get("provenance", {}).get(path, {}).get("source", "assumption")
        drivers.append({"scope": scope, "field": key, "index": item_index, "label": path, "unit": unit,
                        "source": source, "base_value": value, "perturbed_value": perturbed,
                        "perturbation": perturbed - value,
                        "terminal_cash_delta_chf": round(changed["closing_cash_chf"] - base["closing_cash_chf"], 2),
                        "real_terminal_cash_delta_chf": round(changed["closing_real_cash_chf"] - base["closing_real_cash_chf"], 2),
                        "minimum_liquidity_delta_chf": round(changed["min_cash_chf"] - base["min_cash_chf"], 2),
                        "goal_impacts": impacts})
    name = "Branch" if nodes[-1]["branch_id"] not in ("root", "main") else "Simulation"
    commitments = next((node["branch_commitments"] for node in reversed(nodes) if "branch_commitments" in node), None)
    commitments = commitments or {"inherited": scenario, "replaced": [], "new": []}
    path = {"timeline": _timeline(nodes), "metrics": base, "events": scenario["one_off_costs"],
            "commitments": commitments, "provenance": scenario["provenance"], "sensitivity": drivers}
    limitations = ["Results are conditional on the supplied scenario and do not arrange financing.",
                   "Sensitivity changes one input at a time after the latest branch or scenario edit, with the earlier path held fixed.",
                   "For fixed-term debt, changing principal, rate or term assumes refinancing the outstanding balance over the stated term."]
    if not replay_ok:
        limitations.append("Sensitivity is unavailable because the latest segment could not be replayed from the supplied checkpoint.")
    record = {"scenario": scenario, "checkpoint": checkpoint, "horizon_months": months,
              "sensitivity_start_month": checkpoint["month"]}
    return build_comparison_bundle({name: path}, baseline_path=name, scenario_record=record,
                                   assumptions=scenario["provenance"], comparisons={}, limitations=limitations)
