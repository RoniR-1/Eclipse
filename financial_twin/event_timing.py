from copy import deepcopy


def edit_base_event(scenario, kind, index, start_month, duration):
    if isinstance(start_month, bool) or not isinstance(start_month, int) or start_month < 0:
        raise ValueError("Start must be a nonnegative whole month.")
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ValueError("Invalid event index.")
    revised = deepcopy(scenario)
    if kind == "cost":
        costs = revised.get("one_off_costs", [])
        if index >= len(costs):
            raise ValueError("Event could not be found.")
        costs[index]["month"] = start_month
        return revised
    if kind != "phase":
        raise ValueError("Unknown event type.")
    if isinstance(duration, bool) or not isinstance(duration, int) or duration < 0:
        raise ValueError("Duration must be a nonnegative whole number of months.")
    phases = revised.get("phases", [])
    if index >= len(phases):
        raise ValueError("Event could not be found.")
    ordered = sorted(range(len(phases)), key=lambda i: phases[i]["start_month"])
    position = ordered.index(index)
    phase = phases[index]
    previous = phases[ordered[position - 1]] if position else None
    following = phases[ordered[position + 1]] if position + 1 < len(ordered) else None
    if previous is None and start_month != 0:
        raise ValueError("The first phase must start at the beginning of the timeline.")
    if previous is not None and start_month <= previous["start_month"]:
        raise ValueError("The start must follow the previous phase.")
    end_month = start_month + duration if duration else None
    if following is not None:
        if end_month is None:
            raise ValueError("A phase followed by another phase must have a duration.")
        if following["end_month"] is not None and end_month >= following["end_month"]:
            raise ValueError("The end must precede the end of the next phase.")
        following["start_month"] = end_month
    elif end_month is not None:
        continuation = deepcopy(previous if previous is not None else phase)
        continuation["start_month"] = end_month
        continuation["end_month"] = None
        phases.append(continuation)
    if previous is not None:
        previous["end_month"] = start_month
    phase["start_month"] = start_month
    phase["end_month"] = end_month
    return revised
