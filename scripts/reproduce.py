"""Check that a saved run file reproduces exactly, or write the sample run file.

Usage (from the repository root):

    .local/venv/bin/python scripts/reproduce.py examples/sample-run.json
    .local/venv/bin/python scripts/reproduce.py path/to/downloaded-run.json
    .local/venv/bin/python scripts/reproduce.py --write-sample examples/sample-run.json

Needs neither the AI provider nor the database. Exits with status 0 when every recorded value
is reproduced exactly and 1 otherwise.
"""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from financial_twin.reproduce import build_run, load, verify  # noqa: E402
from financial_twin.server import apply_branch_change, random_test  # noqa: E402

SAMPLE_SEED = 42
SAMPLE_START = "2026-01-01"


def sample_run():
    """Seeded test data, one extra user branch with a recorded change, and one deleted branch."""
    test = random_test({"years": 5, "seed": SAMPLE_SEED, "start_date": SAMPLE_START})
    nodes = test["nodes"]
    by_id = {node["id"]: node for node in nodes}
    main = [node for node in nodes if node["branch_id"] in ("root", "main")]
    change = {"monthly_income_delta_chf": 0, "monthly_expenses_delta_chf": 2400, "one_off_cost_chf": 16000,
              "investment_multiplier": 1, "new_asset_chf": 0, "new_debt_chf": 0,
              "new_debt_annual_interest_rate": 0, "new_debt_monthly_payment_chf": 0}
    added = apply_branch_change(main[30], "Start a Bachelor's degree", 30, {}, change, 100)
    branches = [added["branch"]]
    for node in added["nodes"]:
        by_id[node["id"]] = node
    # Delete the first generated branch (and anything that grew out of it) to show deletions are replayed too.
    deleted_branch = next(node["branch_id"] for node in nodes if node["branch_id"] not in ("root", "main"))
    removed = set()
    for node in sorted(by_id.values(), key=lambda item: item["month"]):
        if node["branch_id"] == deleted_branch or node["parent_id"] in removed:
            removed.add(node["id"])
    kept = [node_id for node_id in by_id if node_id not in removed]
    return build_run(source="test", years=5, seed=SAMPLE_SEED, start_date=SAMPLE_START,
                     branches=branches, nodes=list(by_id.values()), kept_node_ids=kept)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_file", nargs="?", help="run file to check")
    parser.add_argument("--write-sample", metavar="PATH", help="write the sample run file to PATH and exit")
    args = parser.parse_args()

    if args.write_sample:
        Path(args.write_sample).parent.mkdir(parents=True, exist_ok=True)
        Path(args.write_sample).write_text(json.dumps(sample_run(), indent=1) + "\n", encoding="utf-8")
        print(f"Wrote {args.write_sample}")
        return 0
    if not args.run_file:
        parser.error("give a run file to check, or --write-sample PATH")

    run = load(args.run_file)
    report = verify(run)
    env = report["environment"]
    print(f"Run file:     {args.run_file}")
    print(f"Source:       {run['source']}" + (f" (seed {run['seed']}, start {run['start_date']})" if run["source"] == "test" else ""))
    print(f"Horizon:      {run['years']} years, monthly steps, CHF")
    print(f"Branches:     {len(run.get('branches', []))} recorded with their AI-produced changes")
    print(f"Recorded on:  Python {env['recorded'].get('python', '?')}, NumPy {env['recorded'].get('numpy', '?')}")
    print(f"Replayed on:  Python {env['current']['python']}, NumPy {env['current']['numpy']}")
    print(f"Checked:      {report['checked_nodes']} nodes, {report['checked_values']} values")
    if report["ok"]:
        print("Result:       REPRODUCED - every recorded value matches exactly.")
        return 0
    print("Result:       NOT REPRODUCED")
    if report["missing_nodes"]:
        print(f"  {len(report['missing_nodes'])} recorded nodes were not produced, e.g. {report['missing_nodes'][:3]}")
    if report["mismatches"]:
        print(f"  {len(report['mismatches'])} values differ (largest difference {report['max_abs_diff']:.6g}), e.g.:")
        for item in report["mismatches"][:5]:
            print(f"    node {item['id']} {item['field']}: expected {item['expected']!r}, got {item['actual']!r}")
    if not env["same"]:
        print("  Note: the software versions differ from the recording, which can change floating-point results.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
