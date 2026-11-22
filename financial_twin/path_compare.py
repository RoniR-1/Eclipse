"""Compare every path on screen over one common horizon, with a plain-language explanation. No AI.

Each path is judged from the start to the same end date (shorter paths are continued at their last
situation, see goals.extend_to_horizon). Per path:

    net worth and cash at the horizon, and the difference from the original path
    lowest cash and the first month cash is negative (a funding gap)
    break-even: the month a path overtakes the original path's net worth and stays ahead
    why: the net-worth difference from the original path split into the money flows that caused it
         (explain.attribute, shared with the goal levers)

The sentences come from fixed templates filled with these calculated numbers.
"""
from financial_twin.explain import attribute, chf as _chf, describe, signed as _signed
from financial_twin.goals import _paths


def _break_even(chain, main_by_date):
    """First month from which this path's net worth stays at or above the original path's, or None."""
    gaps = [(node["date"][:7], node["state"]["net_worth_chf"] - main_by_date[node["date"][:7]])
            for node in chain if node["date"][:7] in main_by_date]
    if not gaps:
        return None, None
    behind = [index for index, (_, gap) in enumerate(gaps) if gap < -0.5]
    if not behind:
        return "always", gaps[0][0]
    if behind[-1] == len(gaps) - 1:
        return "never", None
    return "overtakes", gaps[behind[-1] + 1][0]


def _explain(row, chain, main_chain, main_by_date):
    sentences = []
    if main_chain is not None and chain is not main_chain:
        difference = row["net_worth_vs_original_chf"]
        direction = "more" if difference >= 0 else "less"
        sentences.append(f"Ends {row['horizon']} with {_chf(row['net_worth_chf'])} net worth, {_chf(difference)} {direction} than the original path.")
        sentences.extend(describe(attribute(chain, main_chain, "net_worth_chf")))
        status, when = _break_even(chain, main_by_date)
        if status == "always":
            sentences.append("It is never behind the original path.")
        elif status == "overtakes":
            sentences.append(f"It falls behind first and overtakes the original path in {when}, staying ahead after that.")
        elif status == "never":
            sentences.append(f"It stays behind the original path up to {row['horizon']}.")
    else:
        sentences.append(f"The original path ends {row['horizon']} with {_chf(row['net_worth_chf'])} net worth.")
    if row["first_shortfall"]:
        sentences.append(f"Cash runs out in {row['first_shortfall']} and is short for {row['months_short']} "
                         f"month{'s' if row['months_short'] != 1 else ''}; its lowest point is {_signed(row['lowest_cash_chf'])} in "
                         f"{row['lowest_cash_date']}. The model does not borrow for you: this is a funding gap.")
    else:
        sentences.append(f"Cash never runs out; its lowest point is {_chf(row['lowest_cash_chf'])} in {row['lowest_cash_date']}.")
    if row.get("net_worth_range_chf"):
        low, high = row["net_worth_range_chf"]
        sentences.append(f"If estimates are off, net worth at the horizon could be anywhere from {_chf(low)} to {_chf(high)}.")
    if row["continued_from"]:
        sentences.append(f"This path ends in {row['continued_from']}; it is continued at its last situation so every path is compared over the same years.")
    return sentences


def compare_paths(nodes, ranges=None):
    """Every path over the common horizon, original path first, then by net worth at the horizon."""
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("Run a simulation first.")
    paths = _paths(nodes)
    horizon = max(chain[-1]["date"][:7] for chain in paths.values())
    main_chain = paths.get("main") or paths.get("root")
    main_by_date = {node["date"][:7]: node["state"]["net_worth_chf"] for node in main_chain} if main_chain else {}
    main_end = main_chain[-1]["state"] if main_chain else None
    rows = []
    for branch, chain in paths.items():
        end = chain[-1]
        lowest = min(chain, key=lambda node: node["state"]["cash_chf"])
        short = [node for node in chain[1:] if node["state"]["cash_chf"] < 0]
        last_shown = next(node for node in reversed(chain) if not node.get("continued"))
        row = {
            "branch_id": branch, "tip_id": last_shown["id"], "horizon": horizon,
            "is_original": chain is main_chain,
            "net_worth_chf": round(end["state"]["net_worth_chf"], 2),
            "cash_chf": round(end["state"]["cash_chf"], 2),
            "net_worth_vs_original_chf": round(end["state"]["net_worth_chf"] - main_end["net_worth_chf"], 2) if main_end else None,
            "cash_vs_original_chf": round(end["state"]["cash_chf"] - main_end["cash_chf"], 2) if main_end else None,
            "lowest_cash_chf": round(lowest["state"]["cash_chf"], 2), "lowest_cash_date": lowest["date"][:7],
            "first_shortfall": short[0]["date"][:7] if short else None, "months_short": len(short),
            "continued_from": last_shown["date"][:7] if end.get("continued") else None,
        }
        span = (ranges or {}).get(end["id"], {}).get("net_worth")
        if span:
            row["net_worth_range_chf"] = [round(span[0], 2), round(span[1], 2)]
        status, when = _break_even(chain, main_by_date) if chain is not main_chain else (None, None)
        row["break_even"] = {"status": status, "date": when}
        row["explanation"] = _explain(row, chain, main_chain, main_by_date)
        rows.append(row)
    rows.sort(key=lambda row: (not row["is_original"], -row["net_worth_chf"]))
    return {"horizon": horizon, "paths": rows,
            "method": (f"Every path is compared up to {horizon}; paths that end earlier are continued at their last situation. "
                       "Differences are against the original path at the same date. The explanation splits the net-worth "
                       "difference into the money flows that caused it; buying a home or car moves cash into the item, so "
                       "its price shows up as a one-off cost balanced by the value of the thing bought.")}
