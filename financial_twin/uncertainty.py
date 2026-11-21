"""Uncertainty range for every node on screen: how far a path could move if its inputs are off.

The calculation is deterministic. It re-simulates the whole timeline tree month by month (the same
way /api/edit-nodes does) with adjusted inputs. Every monthly amount is split into today's situation
(the first phase of the starting point, which users know) and the change since (phases and events,
whose amounts are estimates):

    Today's income       fixed by default
    Today's living costs ±5%
    Event effects        ±15% of each change since today (a raise, tuition, a rent change ...)
    One-off costs        ±15%
    Investment returns   − / + volatility / √years (the uncertainty of the average return over the horizon)
    Inflation            ±1 percentage point, whichever side lowers final net worth is "pessimistic"

The pessimistic run moves every group to its unfavourable side at once and the optimistic run to its
favourable side, so the range is an outer bound, not a probability. Each group is also moved on its
own to rank which input matters most. Ranges are centred on the values already on screen: a node's
low value is its shown value plus (pessimistic − unadjusted re-simulation).
"""
from copy import deepcopy
import math

from financial_twin.engine import create_root, is_investment_asset, simulate

DEFAULT_UNCERTAINTY = 0.15            # event effects and one-off costs
RATES = {"income": 0.0, "living_costs": 0.05}   # today's situation
INFLATION_STEP = 0.01
GROUPS = ("event_effects", "one_off_costs", "living_costs", "income", "investment_returns", "inflation")
LABELS = {"event_effects": "Event effects", "one_off_costs": "One-off costs", "living_costs": "Today's living costs",
          "income": "Today's income", "investment_returns": "Investment returns", "inflation": "Inflation"}


def _ordered(nodes):
    """Validate the tree and return its nodes parents-first."""
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("Provide the timeline nodes.")
    by_id = {}
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("state"), dict) or "scenario" not in node:
            raise ValueError("Invalid timeline nodes.")
        by_id[node["id"]] = node
    if len(by_id) != len(nodes):
        raise ValueError("Invalid timeline nodes.")
    children = {}
    for node in nodes:
        parent = node.get("parent_id")
        if parent is not None and parent not in by_id:
            raise ValueError("Invalid timeline structure.")
        children.setdefault(parent, []).append(node)
    order, queue = [], list(children.get(None, []))
    while queue:
        node = queue.pop(0)
        order.append(node)
        queue.extend(children.get(node["id"], []))
    if len(order) != len(nodes):
        raise ValueError("Invalid timeline structure.")
    return by_id, order


def _adjust_scenario(scenario, shift, today):
    """`today` = (income, expenses) of the starting point; anything beyond it is an event effect."""
    scenario = deepcopy(scenario)
    base_income, base_costs = today
    change = shift["event_effects"]
    for phase in scenario["phases"]:
        income_change = phase["monthly_income_chf"] - base_income
        cost_change = phase["monthly_expenses_chf"] - base_costs
        # A favourable event effect means a bit more income and a bit lower costs, whatever the sign of the change.
        phase["monthly_income_chf"] = max(0.0, base_income * (1 + shift["income"]) + income_change + change * abs(income_change))
        phase["monthly_expenses_chf"] = max(0.0, base_costs * (1 + shift["living_costs"]) + cost_change - change * abs(cost_change))
    for cost in scenario["one_off_costs"]:
        cost["amount_chf"] *= 1 + shift["one_off_costs"]
    step = shift["inflation"]
    if step:
        scenario["annual_inflation"] = max(scenario["annual_inflation"] + step, -0.99)
        scenario["inflation_by_year"] = {year: max(rate + step, -0.99)
                                         for year, rate in scenario.get("inflation_by_year", {}).items()}
    for asset in scenario.get("assets", []):
        _adjust_return(asset, shift["investment_returns"])
    return scenario


def _adjust_return(asset, side):
    if side and is_investment_asset(asset):
        asset["annual_return"] = max(asset.get("annual_return", 0) + side * asset.get("annual_volatility", 0), -0.99)


def _adjust_preferences(preferences, side):
    preferences = deepcopy(preferences)
    if side:
        volatility = preferences.get("investment_annual_volatility", 0)
        preferences["investment_annual_return"] = max(preferences.get("investment_annual_return", 0) + side * volatility, -0.99)
    return preferences


def _event_state(original, old):
    """The holdings a branch added or changed; reconstructed for branches saved without it (as /api/edit-nodes does)."""
    event_state = original.get("event_state")
    parent = old[original["parent_id"]]
    if event_state or original["branch_id"] == "main" or original["branch_id"] == parent["branch_id"]:
        return event_state
    after_assets = deepcopy(original["state"]["assets"])
    for asset in after_assets:
        contribution = original["state"]["cash_flow"]["investment_contribution_chf"] if asset["id"] == "__contributions__" else 0
        asset["value_chf"] = (max(0, asset["value_chf"] - contribution) / ((1 + asset["annual_return"]) ** (1 / 12))
                              if asset["annual_return"] > -1 else 0)
    after_debts = deepcopy(original["state"]["debts"])
    for debt in after_debts:
        if debt["principal_chf"] > 0:
            debt["principal_chf"] = (debt["principal_chf"] + debt["monthly_payment_chf"]) / (1 + debt["annual_interest_rate"] / 12)
    return {"assets": after_assets, "debts": after_debts,
            "parent_assets": parent["state"]["assets"], "parent_debts": parent["state"]["debts"]}


def resimulate(old, order, shift):
    """Re-run every node one month at a time with adjusted inputs. Returns {node_id: node}."""
    first = order[0]["scenario"]["phases"][0]
    today = (first["monthly_income_chf"], first["monthly_expenses_chf"])
    result = {}
    for original in order:
        scenario = _adjust_scenario(original["scenario"], shift, today)
        if original.get("parent_id") is None:
            node = create_root(scenario)
        else:
            parent = deepcopy(result[original["parent_id"]])
            event_state = _event_state(original, old)
            if event_state:
                for kind in ("assets", "debts"):
                    before = {item["id"]: item for item in event_state["parent_" + kind]}
                    after = {item["id"]: item for item in event_state[kind]}
                    value_key = "value_chf" if kind == "assets" else "principal_chf"
                    for item in parent["state"][kind]:
                        if item["id"] in before and item["id"] in after and before[item["id"]][value_key]:
                            item[value_key] *= after[item["id"]][value_key] / before[item["id"]][value_key]
                    for key, item in after.items():
                        if key not in before:
                            added = deepcopy(item)
                            if kind == "assets":
                                _adjust_return(added, shift["investment_returns"])
                            parent["state"][kind].append(added)
            parent["scenario"] = scenario
            preferences = _adjust_preferences(original["state"]["active_variables"]["preferences"], shift["investment_returns"])
            node = simulate(parent, 1, preferences=preferences, branch_id=original["branch_id"])[0]
        node["id"], node["parent_id"] = original["id"], original.get("parent_id")
        result[original["id"]] = node
    return result


def _shift(**sides):
    """Signed adjustments; `sides` holds −1/0/+1 per group, where +1 is the favourable direction."""
    return {group: sides.get(group, 0) for group in GROUPS}


def _scaled(sides, uncertainty, inflation_sign, return_scale=1.0):
    """Turn favourable(+1)/unfavourable(−1) sides into the numeric adjustment of each input.

    `return_scale` turns a holding's year-to-year volatility into the uncertainty of its average return
    over the horizon (volatility / √years): good and bad years partly cancel out.
    """
    return {
        "event_effects": sides["event_effects"] * uncertainty,
        "one_off_costs": -sides["one_off_costs"] * uncertainty,
        "income": sides["income"] * RATES["income"],
        "living_costs": -sides["living_costs"] * RATES["living_costs"],
        "investment_returns": sides["investment_returns"] * return_scale,
        "inflation": sides["inflation"] * inflation_sign * INFLATION_STEP,
    }


def _tips(order):
    """Last node of every branch, keyed by branch_id."""
    tips = {}
    for node in order:
        current = tips.get(node["branch_id"])
        if current is None or node["month"] >= current["month"]:
            tips[node["branch_id"]] = node
    return tips


def analyse(nodes, uncertainty=DEFAULT_UNCERTAINTY):
    """Range per node, first pessimistic shortfall per branch, and the inputs that matter most per branch."""
    if isinstance(uncertainty, bool) or not isinstance(uncertainty, (int, float)) or not 0 <= uncertainty <= 0.5:
        raise ValueError("Uncertainty must be between 0 and 0.5.")
    old, order = _ordered(nodes)
    tips = _tips(order)
    horizon_years = max(1.0, max(node["month"] for node in order) / 12)
    return_scale = 1 / math.sqrt(horizon_years)
    base = resimulate(old, order, _scaled(_shift(), uncertainty, 1))

    def tip_worth(run):
        return sum(run[tip["id"]]["state"]["net_worth_chf"] for tip in tips.values())

    # Inflation's effect depends on the plan (indexed income vs. nominal loans), so find its worse side first.
    higher = resimulate(old, order, _scaled(_shift(inflation=1), uncertainty, 1))
    inflation_sign = 1 if tip_worth(higher) >= tip_worth(base) else -1

    def run(**sides):
        return resimulate(old, order, _scaled(_shift(**sides), uncertainty, inflation_sign, return_scale))

    pessimistic = run(**{group: -1 for group in GROUPS})
    optimistic = run(**{group: 1 for group in GROUPS})

    ranges = {}
    for node_id, node in old.items():
        values = {}
        for field in ("cash_chf", "net_worth_chf"):
            shown, centre = node["state"][field], base[node_id]["state"][field]
            low = shown + pessimistic[node_id]["state"][field] - centre
            high = shown + optimistic[node_id]["state"][field] - centre
            values[field.removesuffix("_chf")] = [round(min(shown, low, high), 2), round(max(shown, low, high), 2)]
        ranges[node_id] = values

    shortfalls = {}
    for node in order:
        if ranges[node["id"]]["cash"][0] < 0 and node["branch_id"] not in shortfalls:
            shortfalls[node["branch_id"]] = {"node_id": node["id"], "date": node["date"], "month": node["month"]}

    drivers = {branch: [] for branch in tips}
    for group in GROUPS:
        if group in RATES and not RATES[group]:
            continue   # held fixed, nothing to rank
        down, up = run(**{group: -1}), run(**{group: 1})
        for branch, tip in tips.items():
            centre = base[tip["id"]]["state"]["net_worth_chf"]
            drivers[branch].append({"group": group, "label": LABELS[group],
                                    "low": round(down[tip["id"]]["state"]["net_worth_chf"] - centre, 2),
                                    "high": round(up[tip["id"]]["state"]["net_worth_chf"] - centre, 2)})
    for items in drivers.values():
        items.sort(key=lambda item: abs(item["high"] - item["low"]), reverse=True)

    return {
        "uncertainty": uncertainty,
        "inflation_step": INFLATION_STEP,
        "nodes": ranges,
        "first_shortfall": shortfalls,
        "drivers": drivers,
        "rates": {"event_effects": uncertainty, "one_off_costs": uncertainty, **RATES},
        "note": (f"Range if the effects of events and one-off costs are {uncertainty:.0%} larger or smaller, today's living "
                 f"costs {RATES['living_costs']:.0%} higher or lower, investment returns by their volatility / √years and "
                 f"inflation by {INFLATION_STEP:.0%} point, all at once. Today's income is held fixed. "
                 "It is an outer bound, not a probability."),
    }
