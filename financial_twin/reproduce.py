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
from collections import Counter
from copy import deepcopy

from financial_twin.branching import apply_proposal
from financial_twin.engine import LEGACY_PROVENANCE_IDS
from financial_twin.server import VARIABLES, apply_branch_change, edit_nodes, environment, random_test, run_simulation

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
              branches=(), operations=None, nodes, kept_node_ids=None):
    """Assemble a run file from engine inputs and the nodes they produced (used for the sample and tests)."""
    kept = set(kept_node_ids) if kept_node_ids is not None else {node["id"] for node in nodes}
    recorded_branches = deepcopy(list(branches))
    recorded_operations = deepcopy(operations) if operations is not None else [
        {"type": "branch", "branch": deepcopy(branch)} for branch in recorded_branches
    ]
    return {
        "format": RUN_FORMAT,
        "source": source,
        "seed": seed,
        "start_date": start_date,
        "years": years,
        "units": {"currency": "CHF", "time_step": "month", "horizon_years": years},
        "scenario": scenario,
        "variables": variables or {},
        "branches": recorded_branches,
        "operations": recorded_operations,
        "kept_node_ids": sorted(kept),
        "expected": [node_values(node) for node in nodes if node["id"] in kept],
        "environment": environment(),
    }


def replay(run):
    """Recompute every node of a run from its recorded inputs. Returns {node_id: node}."""
    token = LEGACY_PROVENANCE_IDS.set("operations" not in run)
    try:
        return _replay(run)
    finally:
        LEGACY_PROVENANCE_IDS.reset(token)


def _replay(run):
    if run.get("format") != RUN_FORMAT:
        raise ValueError(f"Unsupported run file format: {run.get('format')!r} (expected {RUN_FORMAT!r}).")
    if run["source"] == "test":
        nodes = random_test({"years": run["years"], "seed": run["seed"], "start_date": run["start_date"]})["nodes"]
    elif run["source"] == "simulate":
        nodes = run_simulation({"scenario": run["scenario"], "years": run["years"], "variables": run["variables"]})["nodes"]
    else:
        raise ValueError(f"Unknown run source: {run['source']!r}.")
    by_id = {node["id"]: node for node in nodes}
    operations = run.get("operations")
    if operations is None or not operations and run.get("branches"):
        operations = [{"type": "branch", "branch": branch} for branch in run.get("branches", [])]
    for operation in operations:
        if operation.get("type") == "edit":
            edited = edit_nodes({"nodes": list(by_id.values()), "node_id": operation["node_id"], "edits": operation["edits"]})
            by_id.update({node["id"]: node for node in edited["nodes"]})
            continue
        if operation.get("type") != "branch":
            raise ValueError(f"Unknown run operation: {operation.get('type')!r}.")
        branch = operation["branch"]
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
    if not isinstance(run, dict):
        raise ValueError("Run file must contain a JSON object.")
    kept_ids = run.get("kept_node_ids")
    if not isinstance(kept_ids, list) or not kept_ids or any(not isinstance(item, str) or not item for item in kept_ids):
        raise ValueError("kept_node_ids must be a non-empty list of node IDs.")
    rows = run.get("expected")
    if not isinstance(rows, list) or not rows:
        raise ValueError("expected must be a non-empty list of node rows.")
    for index, item in enumerate(rows):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            raise ValueError(f"Expected row {index} must be an object with a non-empty string id.")
    replayed = replay(run)
    kept = set(kept_ids)
    missing = sorted(node_id for node_id in kept if node_id not in replayed)
    mismatches = []
    max_abs_diff = 0.0
    expected_ids = [item.get("id") for item in run["expected"]]
    expected_set = set(expected_ids)
    expected_fields = {"id", "branch_id", "month", "date", *EXPECTED_FIELDS, "net_cash_flow_chf"}
    invalid_rows = [index for index, item in enumerate(run["expected"]) if set(item) != expected_fields]
    duplicate_expected_ids = sorted((node_id for node_id, count in Counter(expected_ids).items() if count > 1), key=str)
    duplicate_kept_ids = sorted((node_id for node_id, count in Counter(kept_ids or []).items() if count > 1), key=str)
    missing_expected_ids = sorted(kept - expected_set)
    unexpected_expected_ids = sorted(expected_set - kept, key=str)
    for expected in run["expected"]:
        node = replayed.get(expected["id"])
        if node is None:
            continue
        actual = node_values(node)
        for key, value in expected.items():
            if key not in actual:
                continue
            if isinstance(value, bool) != isinstance(actual[key], bool) or actual[key] != value:
                mismatches.append({"id": expected["id"], "field": key, "expected": value, "actual": actual[key]})
                if isinstance(value, (int, float)) and isinstance(actual[key], (int, float)):
                    max_abs_diff = max(max_abs_diff, abs(actual[key] - value))
    current = environment()
    recorded = run.get("environment") or {}
    return {
        "ok": not any((missing, mismatches, invalid_rows, duplicate_expected_ids, duplicate_kept_ids,
                       missing_expected_ids, unexpected_expected_ids)),
        "checked_nodes": len(run["expected"]),
        "checked_values": sum(len(item) for item in run["expected"]),
        "missing_nodes": missing,
        "missing_expected_ids": missing_expected_ids,
        "unexpected_expected_ids": unexpected_expected_ids,
        "duplicate_expected_ids": duplicate_expected_ids,
        "duplicate_kept_ids": duplicate_kept_ids,
        "invalid_expected_rows": invalid_rows,
        "mismatches": mismatches,
        "max_abs_diff": max_abs_diff,
        "environment": {"recorded": recorded, "current": current,
                        "same": all(recorded.get(key) == value for key, value in current.items())},
    }


def load(path):
    with open(path, encoding="utf-8") as source:
        return json.load(source)
