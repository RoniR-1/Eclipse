"""Runs the browser-logic tests (tests/web, Node's built-in test runner) and checks that a run file
written by the browser's code replays exactly in Python. Skipped when Node.js is not installed."""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from financial_twin.reproduce import verify
from financial_twin.server import alexia_demo, apply_branch_change, edit_nodes, random_test

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "Node.js is not installed")
class FrontendTests(unittest.TestCase):
    def test_browser_logic(self):
        result = subprocess.run([NODE, "--test", "tests/web/app.test.mjs"], cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-2000:])

    def browser_run_file(self, delete_branch=None):
        data = random_test({"years": 5, "seed": 7, "start_date": "2026-01-01"})
        change = {"monthly_income_delta_chf": -2500.0, "monthly_expenses_delta_chf": 300.5, "one_off_cost_chf": 730.0,
                  "investment_multiplier": 1.0, "new_asset_chf": 0, "new_debt_chf": 0,
                  "new_debt_annual_interest_rate": 0, "new_debt_monthly_payment_chf": 0}
        added = apply_branch_change(data["nodes"][14], "Start a Bachelor's", 24, {"savings_rate": 0.25}, change, data["next_sequence"])
        state = {"run": {"source": "test", "seed": data["seed"], "start_date": data["start_date"], "years": 5, "scenario": None,
                         "variables": {}, "branches": [added["branch"]], "next_sequence": data["next_sequence"] + 1,
                         "environment": data["environment"]},
                 "nodes": data["nodes"] + added["nodes"]}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "state.json")
            path.write_text(json.dumps(state), encoding="utf-8")
            command = [NODE, "tests/web/export-run.mjs", str(path)] + ([delete_branch] if delete_branch else [])
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout), state

    def test_browser_run_file_replays_exactly_in_python(self):
        run, state = self.browser_run_file()
        report = verify(run)
        self.assertTrue(report["ok"], report["mismatches"][:3] or report["missing_nodes"][:3])
        self.assertEqual(report["checked_nodes"], len(state["nodes"]))

    def test_browser_deletions_are_respected_on_replay(self):
        first_branch = next(n["branch_id"] for n in random_test({"years": 5, "seed": 7, "start_date": "2026-01-01"})["nodes"]
                            if n["branch_id"] not in ("root", "main"))
        run, state = self.browser_run_file(delete_branch=first_branch)
        report = verify(run)
        self.assertTrue(report["ok"], report["mismatches"][:3] or report["missing_nodes"][:3])
        self.assertLess(report["checked_nodes"], len(state["nodes"]))
        self.assertNotIn(first_branch, {item["branch_id"] for item in run["expected"]})

    def test_browser_export_replays_interleaved_edits_branches_and_deletion(self):
        demo = alexia_demo()
        nodes = demo["nodes"]
        edit = {"cash_chf": 11000}
        updated = edit_nodes({"nodes": nodes, "node_id": nodes[0]["id"], "edits": edit})["nodes"]
        by_id = {node["id"]: node for node in nodes}
        by_id.update({node["id"]: node for node in updated})
        demo["run"]["operations"].append({"type": "edit", "node_id": nodes[0]["id"], "edits": edit})
        change = {"monthly_income_delta_chf": 500, "monthly_expenses_delta_chf": 0, "one_off_cost_chf": 1000,
                  "investment_multiplier": 1, "new_asset_chf": 0, "new_debt_chf": 0,
                  "new_debt_annual_interest_rate": 0, "new_debt_monthly_payment_chf": 0}
        added = apply_branch_change(by_id[nodes[12]["id"]], "New role", 24, {}, change, 1)
        demo["run"]["branches"].append(added["branch"])
        demo["run"]["operations"].append({"type": "branch", "branch": added["branch"]})
        by_id.update({node["id"]: node for node in added["nodes"]})
        deleted = demo["nodes"][-1]["branch_id"]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "state.json")
            path.write_text(json.dumps({"run": demo["run"], "nodes": list(by_id.values())}), encoding="utf-8")
            result = subprocess.run([NODE, "tests/web/export-run.mjs", str(path), deleted], cwd=ROOT,
                                    capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = verify(json.loads(result.stdout))
        self.assertTrue(report["ok"], report)


if __name__ == "__main__":
    unittest.main()
