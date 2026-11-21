"""Reproducibility checks (LauzHack technical-validation checklist, section 1).

- The same deterministic inputs reproduce the same results.
- Random test data records its seed, and the same seed reproduces it.
- Branches get deterministic IDs, so a saved run can be replayed node for node.
- A saved run file replays exactly, including after a browser-style JSON round trip,
  and a tampered file is detected.
"""

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

from financial_twin.reproduce import RUN_FORMAT, build_run, replay, verify
from financial_twin.server import apply_branch_change, edit_nodes, random_test, run_simulation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

CHANGE = {"monthly_income_delta_chf": 0, "monthly_expenses_delta_chf": 2400.0, "one_off_cost_chf": 16000.0,
          "investment_multiplier": 1.0, "new_asset_chf": 0, "new_debt_chf": 0,
          "new_debt_annual_interest_rate": 0, "new_debt_monthly_payment_chf": 0}


def scenario():
    return {
        "start_date": "2026-01-01", "currency": "CHF", "opening_cash_chf": 8000, "annual_inflation": 0.015,
        "phases": [{"name": "Study", "start_month": 0, "end_month": 24, "monthly_income_chf": 1200, "monthly_expenses_chf": 2100},
                   {"name": "Work", "start_month": 24, "end_month": None, "monthly_income_chf": 5200, "monthly_expenses_chf": 3300}],
        "one_off_costs": [{"description": "Tuition", "month": 8, "amount_chf": 730}],
        "assets": [{"id": "etf", "type": "investment", "value_chf": 3000, "annual_return": 0.05, "annual_volatility": 0.15}],
        "debts": [{"id": "loan", "principal_chf": 6000, "annual_interest_rate": 0.03, "monthly_payment_chf": 250}],
        "preferences": {"savings_rate": 0.5, "risk_tolerance": 0.6, "safety_priority": 0.5, "emergency_months": 3,
                        "investment_annual_return": 0.04, "investment_annual_volatility": 0.15},
    }


def browser_round_trip(value):
    """Serialize like a browser would: integral floats lose their '.0' (JavaScript has one number type)."""
    def strip(item):
        if isinstance(item, float) and item.is_integer() and abs(item) < 2 ** 53:
            return int(item)
        if isinstance(item, dict):
            return {key: strip(val) for key, val in item.items()}
        if isinstance(item, list):
            return [strip(val) for val in item]
        return item
    return json.loads(json.dumps(strip(value)))


class ReproducibilityTests(unittest.TestCase):
    def test_same_inputs_give_identical_results(self):
        data = {"scenario": scenario(), "years": 5, "variables": {"opening_cash_chf": 9000, "savings_rate": 0.4, "risk_tolerance": 0.7}}
        first = run_simulation(deepcopy(data))["nodes"]
        second = run_simulation(deepcopy(data))["nodes"]
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))

    def test_test_data_records_its_seed_and_reproduces_from_it(self):
        generated = random_test({"years": 5})
        self.assertIsInstance(generated["seed"], int)
        again = random_test({"years": 5, "seed": generated["seed"], "start_date": generated["start_date"]})
        self.assertEqual(generated["nodes"], again["nodes"])
        other = random_test({"years": 5, "seed": generated["seed"] + 1, "start_date": generated["start_date"]})
        self.assertNotEqual([n["state"]["cash_chf"] for n in generated["nodes"]], [n["state"]["cash_chf"] for n in other["nodes"]])

    def test_branch_ids_are_deterministic_with_a_sequence_number(self):
        parent = run_simulation({"scenario": scenario(), "years": 3, "variables": {"opening_cash_chf": 8000, "savings_rate": 0.5, "risk_tolerance": 0.6}})["nodes"][12]
        first = apply_branch_change(parent, "Start a Bachelor's degree", 12, {}, CHANGE, 0)
        again = apply_branch_change(deepcopy(parent), "Start a Bachelor's degree", 12, {}, deepcopy(CHANGE), 0)
        self.assertEqual(first["nodes"], again["nodes"])
        sibling = apply_branch_change(parent, "Start a Bachelor's degree", 12, {}, CHANGE, 1)
        self.assertNotEqual(first["nodes"][0]["id"], sibling["nodes"][0]["id"])
        self.assertEqual([n["state"] for n in first["nodes"]], [n["state"] for n in sibling["nodes"]])
        # Without a sequence number (live requests from older clients) IDs stay unique per request.
        self.assertNotEqual(apply_branch_change(parent, "x", 2, {}, CHANGE)["nodes"][0]["id"],
                            apply_branch_change(parent, "x", 2, {}, CHANGE)["nodes"][0]["id"])

    def simulated_run(self):
        data = {"scenario": scenario(), "years": 5, "variables": {"opening_cash_chf": 9000, "savings_rate": 0.4, "risk_tolerance": 0.7}}
        nodes = run_simulation(deepcopy(data))["nodes"]
        branch = apply_branch_change(nodes[18], "Start a Bachelor's degree", 24, {"savings_rate": 0.2}, CHANGE, 0)
        nested = apply_branch_change(branch["nodes"][6], "Part-time job", 12, {}, {**CHANGE, "monthly_income_delta_chf": 900.0, "one_off_cost_chf": 0}, 1)
        all_nodes = nodes + branch["nodes"] + nested["nodes"]
        return build_run(source="simulate", years=5, scenario=data["scenario"], variables=data["variables"],
                         branches=[branch["branch"], nested["branch"]], nodes=all_nodes)

    def test_run_file_replays_exactly_after_a_browser_round_trip(self):
        run = browser_round_trip(self.simulated_run())
        self.assertEqual(run["format"], RUN_FORMAT)
        report = verify(run)
        self.assertTrue(report["ok"], report["mismatches"][:3] or report["missing_nodes"][:3])
        self.assertEqual(report["checked_nodes"], 61 + 24 + 12)

    def test_deleted_nodes_stay_deleted_on_replay(self):
        run = self.simulated_run()
        nested_ids = {item["id"] for item in run["expected"] if item["branch_id"] == run["expected"][-1]["branch_id"]}
        run["kept_node_ids"] = [node_id for node_id in run["kept_node_ids"] if node_id not in nested_ids]
        run["expected"] = [item for item in run["expected"] if item["id"] not in nested_ids]
        self.assertTrue(verify(run)["ok"])
        self.assertTrue(nested_ids <= set(replay(run)))  # recomputed, but not part of the kept result

    def test_tampered_run_file_is_detected(self):
        run = self.simulated_run()
        run["expected"][40]["net_worth_chf"] += 0.01
        report = verify(run)
        self.assertFalse(report["ok"])
        self.assertEqual(report["mismatches"][0]["field"], "net_worth_chf")
        self.assertAlmostEqual(report["max_abs_diff"], 0.01)

    def test_edits_and_branches_replay_in_order(self):
        data = {"scenario": scenario(), "years": 2, "variables": {"opening_cash_chf": 8000, "savings_rate": 0.5, "risk_tolerance": 0.6}}
        nodes = run_simulation(deepcopy(data))["nodes"]
        first_edit = {"cash_chf": 12000}
        nodes = edit_nodes({"nodes": nodes, "node_id": nodes[0]["id"], "edits": first_edit})["nodes"]
        branch = apply_branch_change(nodes[6], "Study", 6, {}, CHANGE, 0)
        nodes.extend(branch["nodes"])
        second_edit = {"monthly_income_chf": 2300}
        updated = edit_nodes({"nodes": nodes, "node_id": branch["nodes"][2]["id"], "edits": second_edit})["nodes"]
        by_id = {node["id"]: node for node in nodes}
        by_id.update({node["id"]: node for node in updated})
        run = build_run(source="simulate", years=2, scenario=data["scenario"], variables=data["variables"], nodes=list(by_id.values()), branches=[branch["branch"]])
        run["operations"] = [{"type": "edit", "node_id": nodes[0]["id"], "edits": first_edit},
                             {"type": "branch", "branch": branch["branch"]},
                             {"type": "edit", "node_id": branch["nodes"][2]["id"], "edits": second_edit}]
        report = verify(browser_round_trip(run))
        self.assertTrue(report["ok"], report)

    def test_expected_rows_must_be_complete_unique_and_match_kept_ids(self):
        run = self.simulated_run()
        duplicate = deepcopy(run)
        duplicate["expected"][0] = deepcopy(duplicate["expected"][1])
        report = verify(duplicate)
        self.assertFalse(report["ok"])
        self.assertEqual(report["duplicate_expected_ids"], [run["expected"][1]["id"]])
        self.assertEqual(report["missing_expected_ids"], [run["expected"][0]["id"]])
        incomplete = deepcopy(run)
        del incomplete["expected"][0]["cash_chf"]
        self.assertEqual(verify(incomplete)["invalid_expected_rows"], [0])
        duplicate_kept = deepcopy(run)
        duplicate_kept["kept_node_ids"].append(duplicate_kept["kept_node_ids"][0])
        self.assertFalse(verify(duplicate_kept)["ok"])
        boolean = deepcopy(run)
        boolean["expected"][0]["month"] = False
        self.assertFalse(verify(boolean)["ok"])
        for rows in ([], [None], [{}]):
            invalid = deepcopy(run)
            invalid["expected"] = rows
            with self.assertRaisesRegex(ValueError, "expected|Expected"):
                verify(invalid)
        invalid = deepcopy(run)
        invalid["kept_node_ids"] = []
        with self.assertRaisesRegex(ValueError, "kept_node_ids"):
            verify(invalid)

    def test_committed_sample_run_reproduces(self):
        import reproduce as cli
        sample = json.loads((ROOT / "examples" / "sample-run.json").read_text(encoding="utf-8"))
        self.assertTrue(verify(sample)["ok"], "examples/sample-run.json no longer reproduces; if the engine changed "
                                              "on purpose, regenerate it with scripts/reproduce.py --write-sample")
        self.assertEqual(cli.sample_run()["expected"], sample["expected"])
        self.assertEqual(sample["units"], {"currency": "CHF", "time_step": "month", "horizon_years": 5})

    def test_legacy_integral_provenance_run_reproduces(self):
        path = ROOT / "fixtures" / "demo" / "legacy-integral-provenance-run.json"
        run = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(verify(run)["ok"])
        run["operations"] = []
        self.assertFalse(verify(run)["ok"])

    def test_new_run_with_integral_provenance_uses_current_ids(self):
        legacy = json.loads((ROOT / "fixtures" / "demo" / "legacy-integral-provenance-run.json").read_text(encoding="utf-8"))
        nodes = run_simulation({"scenario": legacy["scenario"], "variables": legacy["variables"], "years": 1})["nodes"]
        operations = []
        run = build_run(source="simulate", years=1, scenario=legacy["scenario"], variables=legacy["variables"],
                        nodes=nodes, operations=operations)
        operations.append({"type": "edit", "node_id": nodes[0]["id"], "edits": {"cash_chf": 1}})
        self.assertEqual(run["operations"], [])
        self.assertTrue(verify(browser_round_trip(run))["ok"])


if __name__ == "__main__":
    unittest.main()
