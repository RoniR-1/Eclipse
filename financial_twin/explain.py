"""Why one path ends up different from another, in plain words. No AI.

One attribution is used everywhere a difference is explained: the Compare paths table (net worth at the
horizon) and the "cause" lever of a goal (cash or net worth at the goal's deadline).

The difference between two paths at a date is split into the money flows that caused it, summed from
the start up to that date:

    cash        = income − living costs − one-off costs − loan payments − money moved into investments
                  + money borrowed
    net worth   = income − living costs − one-off costs − loan interest + investment returns
                  + changes in the value of what you own (homes rising, market falls, deposits, sales)

Whatever the listed flows do not cover is reported as the last part, so the parts always add up to the
difference. Buying something with cash is a one-off cost balanced by the value of the thing bought; for
net worth the two are netted and reported as cash moved into things you own, not as a loss.
"""

PARTS = {
    "net_worth_chf": [
        ("income_chf", 1, "income"),
        ("expenses_chf", -1, "living costs"),
        ("one_off_costs_chf", -1, "one-off costs"),
        ("debt_interest_chf", -1, "loan interest"),
        ("investment_return_chf", 1, "investment returns"),
    ],
    "cash_chf": [
        ("income_chf", 1, "income"),
        ("expenses_chf", -1, "living costs"),
        ("one_off_costs_chf", -1, "one-off costs and purchases"),
        ("debt_payments_chf", -1, "loan payments"),
        ("investment_contribution_chf", -1, "money moved into investments"),
        ("borrowing_proceeds_chf", 1, "money borrowed"),
    ],
}
REST = {
    "net_worth_chf": "changes in the value of what you own (home, investments, deposits)",
    "cash_chf": "other cash movements",
}


def chf(value):
    value = abs(value)
    if value >= 10000:
        return f"CHF {value / 1000:,.1f}k".replace(".0k", "k")
    return f"CHF {value:,.0f}"


def signed(value):
    return f"{'+' if value >= 0 else '−'}{chf(value)}"


def _upto(chain, until_key):
    return [node for node in chain if until_key is None or node["date"][:7] <= until_key]


def attribute(chain, base_chain, field="net_worth_chf", until_key=None):
    """Split the difference in `field` between two paths at `until_key` (YYYY-MM, default: the end)."""
    mine, base = _upto(chain, until_key), _upto(base_chain, until_key)
    difference = mine[-1]["state"][field] - base[-1]["state"][field]
    parts = {}
    for key, sign, label in PARTS[field]:
        total = sum(node["state"].get("cash_flow", {}).get(key, 0.0) for node in mine[1:])
        total -= sum(node["state"].get("cash_flow", {}).get(key, 0.0) for node in base[1:])
        parts[label] = sign * total
    rest = difference - sum(parts.values())
    moved = 0.0
    if field == "net_worth_chf" and parts["one-off costs"] < 0 < rest:
        moved = min(-parts["one-off costs"], rest)
        parts["one-off costs"] += moved
        rest -= moved
    parts[REST[field]] = rest
    threshold = max(100.0, 0.02 * abs(difference))
    ordered = sorted(((label, value) for label, value in parts.items() if abs(value) >= threshold),
                     key=lambda part: abs(part[1]), reverse=True)
    return {"difference": difference, "parts": ordered, "moved_into_things": moved if moved >= threshold else 0.0}


def describe(attribution, limit=3):
    """'Working for this path: …' / 'Working against it: …' sentences for the largest parts."""
    sentences = []
    if attribution["moved_into_things"]:
        sentences.append(f"Purchases moved {chf(attribution['moved_into_things'])} of cash into things you own; "
                         "that alone does not change net worth.")
    top = attribution["parts"][:limit]
    helped = [f"{label} ({signed(value)})" for label, value in top if value > 0]
    hurt = [f"{label} ({signed(value)})" for label, value in top if value < 0]
    if helped:
        sentences.append("Working for this path: " + ", ".join(helped) + ".")
    if hurt:
        sentences.append("Working against it: " + ", ".join(hurt) + ".")
    return sentences


def holding_back(attribution, limit=2):
    """The parts that pull a path below the other one, or None."""
    hurt = [f"{label} ({signed(value)})" for label, value in attribution["parts"] if value < 0][:limit]
    return ", ".join(hurt) if hurt else None
