"""Replay a saved run file and check that it reproduces the recorded results exactly.

A run file (format ``financial-twin-run/1``) is what the website's "Download run file" button saves.
It holds every input the calculation engine used, so a run can be recomputed without the AI provider
or the database:

- ``source``: ``"simulate"`` (a scenario the AI extracted from the user's story) or ``"test"``
  (random test data, regenerated from ``seed`` and ``start_date``).
- ``scenario``, ``years``, ``variables``: the engine inputs for the main path.
- ``branches``: every branch in creation order, with what the AI produced for it, so replay does
  not need to ask the AI again: a ``change`` for a one-step branch (/api/branch) or the reviewed
  building-block ``proposal`` (/api/branch/apply). Each branch has a ``sequence`` number that makes its node IDs deterministic.
- ``kept_node_ids``: the nodes still on screen after any deletions.
- ``expected``: the values that were on screen, which replay must match exactly.
- ``environment``: the Python and NumPy versions that produced the results.

The AI steps (story to scenario, event description to change) are not deterministic, which is why the
file records their outputs. Everything after them is deterministic.
"""

from __future__ import annotations

import json

from financial_twin.branching import apply_proposal
from financial_twin.server import VARIABLES, apply_branch_change, environment, random_test, run_simulation

RUN_FORMAT = "financial-twin-run/1"
EXPECTED_FIELDS = ("cash_chf", "net_worth_chf", "total_assets_chf", "total_debt_chf")


def node_values(node):
    """The values a run file records per node: identity, time and the balance-sheet totals."""
    values = {"id": node["id"], "branch_id": node["branch_id"], "month": node["month"], "date": node["date"]}
    for field in EXPECTED_FIELDS:
        values[field] = node["state"][field]
    values["net_cash_flow_chf"] = node["state"]["cash_flow"]["net_cash_flow_chf"]
    return values


def build_run(*, source, years, scenario=None, variables=None, seed=None, start_date=None,
              branches=(), nodes, kept_node_ids=None):
    """Assemble a run file from engine inputs and the nodes they produced (used for the sample and tests)."""
    kept = set(kept_node_ids) if kept_node_ids is not None else {node["id"] for node in nodes}
    return {
        "format": RUN_FORMAT,
        "source": source,
        "seed": seed,
        "start_date": start_date,
        "years": years,
        "units": {"currency": "CHF", "time_step": "month", "horizon_years": years},
        "scenario": scenario,
        "variables": variables or {},
        "branches": list(branches),
        "kept_node_ids": sorted(kept),
        "expected": [node_values(node) for node in nodes if node["id"] in kept],
        "environment": environment(),
    }


def replay(run):
    """Recompute every node of a run from its recorded inputs. Returns {node_id: node}."""
    if run.get("format") != RUN_FORMAT:
        raise ValueError(f"Unsupported run file format: {run.get('format')!r} (expected {RUN_FORMAT!r}).")
    if run["source"] == "test":
        nodes = random_test({"years": run["years"], "seed": run["seed"], "start_date": run["start_date"]})["nodes"]
    elif run["source"] == "simulate":
        nodes = run_simulation({"scenario": run["scenario"], "years": run["years"], "variables": run["variables"]})["nodes"]
    else:
        raise ValueError(f"Unknown run source: {run['source']!r}.")
    by_id = {node["id"]: node for node in nodes}
    for branch in run.get("branches", []):
        parent = by_id.get(branch["parent_id"])
        if parent is None:
            raise ValueError(f"Branch {branch.get('event')!r} starts from unknown node {branch['parent_id']}.")
        if "proposal" in branch:   # a reviewed building-block branch (/api/branch/apply)
            result = apply_proposal(parent, branch["event"], branch["months"], branch.get("variables", {}),
                                    branch["proposal"], VARIABLES, branch["sequence"])
        else:                      # a one-step branch (/api/branch)
            result = apply_branch_change(parent, branch["event"], branch["months"], branch.get("variables", {}),
                                         branch["change"], branch["sequence"],
                                         inferred_transformation=branch.get("inferred_transformation"), include_report=False)
        for node in result["nodes"]:
            by_id[node["id"]] = node
    return by_id


def verify(run):
    """Replay a run and compare it with the recorded values. Returns a report; report["ok"] is the verdict."""
    replayed = replay(run)
    kept = set(run.get("kept_node_ids") or replayed)
    missing = sorted(node_id for node_id in kept if node_id not in replayed)
    mismatches = []
    max_abs_diff = 0.0
    for expected in run["expected"]:
        node = replayed.get(expected["id"])
        if node is None:
            continue
        actual = node_values(node)
        for key, value in expected.items():
            if actual[key] != value:
                mismatches.append({"id": expected["id"], "field": key, "expected": value, "actual": actual[key]})
                if isinstance(value, (int, float)) and isinstance(actual[key], (int, float)):
                    max_abs_diff = max(max_abs_diff, abs(actual[key] - value))
    current = environment()
    recorded = run.get("environment") or {}
    return {
        "ok": not missing and not mismatches and len(run["expected"]) == len(kept),
        "checked_nodes": len(run["expected"]),
        "checked_values": sum(len(item) for item in run["expected"]),
        "missing_nodes": missing,
        "mismatches": mismatches,
        "max_abs_diff": max_abs_diff,
        "environment": {"recorded": recorded, "current": current,
                        "same": all(recorded.get(key) == value for key, value in current.items())},
    }


def load(path):
    with open(path, encoding="utf-8") as source:
        return json.load(source)
