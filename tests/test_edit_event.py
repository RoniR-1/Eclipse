from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from financial_twin.reproduce import build_run, verify
from financial_twin.event_timing import edit_base_event
from financial_twin.server import append_event, apply_branch, edit_event, random_test
from tests.test_append_event import everything, income, tree
from tests.test_building_blocks import change, proposal


class EditEventTests(unittest.TestCase):
    def setUp(self):
        self.tree = tree()
        self.nodes = everything(self.tree)
        self.run = build_run(source="test", years=0, branches=self.tree["records"], nodes=self.nodes)

    def assert_replays(self, result):
        run = build_run(source="test", years=0, branches=result["branches"],
                        operations=result["operations"], nodes=result["nodes"])
        with patch("financial_twin.reproduce.random_test", return_value={"nodes": deepcopy(self.tree["main"])}):
            report = verify(json.loads(json.dumps(run)))
        self.assertTrue(report["ok"], report["mismatches"][:3] or report["missing_nodes"][:3])

    def test_move_and_resize_branch_event_recalculates_and_replays(self):
        with patch("financial_twin.reproduce.random_test", return_value={"nodes": deepcopy(self.tree["main"])}):
            result = edit_event({"run": self.run, "node_id": self.tree["B"][0]["id"],
                                 "parent_id": self.tree["main"][8]["id"], "durations": [3]})
        nodes = {node["id"]: node for node in result["nodes"]}
        first = nodes[result["selected_id"]]
        self.assertEqual(first["parent_id"], self.tree["main"][8]["id"])
        branch = sorted((node for node in nodes.values() if node["branch_id"] == first["branch_id"]),
                        key=lambda node: node["month"])
        self.assertEqual([node["state"]["cash_flow"]["expenses_chf"] for node in branch[:4]],
                         [2100, 2100, 2100, 2000])
        self.assert_replays(result)

    def test_move_and_resize_appended_event_recalculates_and_replays(self):
        point = self.tree["B"][3]
        added = append_event({"nodes": self.nodes, "node_id": point["id"], "event": "Raise",
                              "proposal": proposal(title="Raise", changes=[change("income", 500, duration=6)]),
                              "sequence": 3})
        nodes = {node["id"]: node for node in self.nodes}
        nodes.update({node["id"]: node for node in added["nodes"]})
        run = build_run(source="test", years=0, branches=[*self.tree["records"], added["record"]],
                        operations=[*self.run["operations"], {"type": "append", "append": added["record"]}],
                        nodes=list(nodes.values()))
        with patch("financial_twin.reproduce.random_test", return_value={"nodes": deepcopy(self.tree["main"])}):
            result = edit_event({"run": run, "node_id": added["nodes"][0]["id"],
                                 "event_id": added["appended"]["id"],
                                 "parent_id": self.tree["B"][5]["id"], "durations": [2]})
        changed = {node["id"]: node for node in result["nodes"]}
        self.assertEqual(income(changed[self.tree["B"][5]["id"]]), income(self.tree["B"][5]))
        self.assertEqual(income(changed[self.tree["B"][6]["id"]]), income(self.tree["B"][6]) + 500)
        self.assertEqual(income(changed[self.tree["B"][7]["id"]]), income(self.tree["B"][7]) + 500)
        self.assertEqual(income(changed[self.tree["B"][8]["id"]]), income(self.tree["B"][8]))
        self.assert_replays(result)

    def test_move_extended_event_and_edit_again(self):
        point = self.tree["Alt"][-1]
        added = append_event({"nodes": self.nodes, "node_id": point["id"], "event": "Raise",
                              "proposal": proposal(title="Raise", changes=[change("income", 500, duration=6)]),
                              "sequence": 3, "months": 4})
        nodes = self.nodes + added["nodes"]
        run = build_run(source="test", years=0, branches=[*self.tree["records"], added["record"]],
                        operations=[*self.run["operations"], {"type": "append", "append": added["record"]}], nodes=nodes)
        with patch("financial_twin.reproduce.random_test", return_value={"nodes": deepcopy(self.tree["main"])}):
            result = edit_event({"run": run, "node_id": added["nodes"][0]["id"],
                                 "event_id": added["appended"]["id"],
                                 "parent_id": self.tree["Alt"][-3]["id"], "durations": [2]})
            self.assert_replays(result)
            updated_run = build_run(source="test", years=0, branches=result["branches"],
                                    operations=result["operations"], nodes=result["nodes"])
            selected = next(node for node in result["nodes"] if node["id"] == result["selected_id"])
            again = edit_event({"run": updated_run, "node_id": result["selected_id"],
                                "event_id": selected["appended_events"][0]["id"],
                                "parent_id": self.tree["Alt"][-2]["id"], "durations": [3]})
        self.assert_replays(again)
        revised = {node["id"]: node for node in again["nodes"]}
        self.assertEqual(income(revised[self.tree["Alt"][-1]["id"]]), income(self.tree["Alt"][-1]) + 500)


class EditBaseEventTests(unittest.TestCase):
    def setUp(self):
        self.scenario = {"phases": [
            {"name": "Work", "start_month": 0, "end_month": 12, "monthly_income_chf": 4000},
            {"name": "Study", "start_month": 12, "end_month": 24, "monthly_income_chf": 1000},
            {"name": "Work again", "start_month": 24, "end_month": 36, "monthly_income_chf": 5000},
            {"name": "Retire", "start_month": 36, "end_month": None, "monthly_income_chf": 2000},
        ], "one_off_costs": [{"month": 12, "amount_chf": 1000}]}

    def test_phase_edit_adjusts_adjacent_boundaries(self):
        changed = edit_base_event(self.scenario, "phase", 1, 14, 8)
        self.assertEqual([(p["start_month"], p["end_month"]) for p in changed["phases"]],
                         [(0, 14), (14, 22), (22, 36), (36, None)])
        self.assertEqual(self.scenario["phases"][1]["start_month"], 12)
        self.assertEqual(changed["one_off_costs"][0]["month"], 12)

    def test_finite_last_phase_returns_to_previous_values(self):
        changed = edit_base_event(self.scenario, "phase", 3, 36, 6)
        self.assertEqual(changed["phases"][-2]["end_month"], 42)
        self.assertEqual(changed["phases"][-1]["start_month"], 42)
        self.assertEqual(changed["phases"][-1]["monthly_income_chf"], 5000)
        self.assertIsNone(changed["phases"][-1]["end_month"])

    def test_cost_move_and_invalid_phase_boundaries(self):
        changed = edit_base_event(self.scenario, "cost", 0, 15, None)
        self.assertEqual(changed["one_off_costs"][0]["month"], 15)
        for index, start, duration in [(0, 1, 12), (1, 0, 8), (1, 14, 0), (1, 14, 22)]:
            with self.subTest(index=index, start=start, duration=duration):
                with self.assertRaises(ValueError):
                    edit_base_event(self.scenario, "phase", index, start, duration)


class DemoEventTests(unittest.TestCase):
    def test_legacy_event_timing_expires_and_export_replays(self):
        demo = random_test({"years": 5, "seed": 1, "start_date": "2026-01-01"})
        nodes = demo["nodes"]
        record = demo["branch_records"][0]
        event = next(node for node in nodes if node.get("parent_id") == record["parent_id"]
                     and node.get("event_summary") == record["event"])
        main = {node["month"]: node for node in nodes if node["branch_id"] in ("main", "root")}
        old_parent = next(node for node in nodes if node["id"] == record["parent_id"])
        parent = main[old_parent["month"] + 1]
        run = build_run(source="test", years=5, seed=1, start_date="2026-01-01",
                        branches=demo["branch_records"], nodes=nodes)
        result = edit_event({"run": run, "node_id": event["id"], "parent_id": parent["id"], "durations": [3]})
        selected = next(node for node in result["nodes"] if node["id"] == result["selected_id"])
        branch = sorted((node for node in result["nodes"] if node["branch_id"] == selected["branch_id"]),
                        key=lambda node: node["month"])
        self.assertEqual(selected["parent_id"], parent["id"])
        delta = record["change"]["monthly_income_delta_chf"]
        for index, node in enumerate(branch[:4]):
            self.assertAlmostEqual(income(node), income(main[node["month"]]) + (delta if index < 3 else 0), places=5)
        exported = build_run(source=result["source"], years=5, scenario=result["scenario"],
                             variables=result["variables"], branches=result["branches"],
                             operations=result["operations"], nodes=result["nodes"])
        report = verify(json.loads(json.dumps(exported)))
        self.assertTrue(report["ok"], report["mismatches"][:3] or report["missing_nodes"][:3])


if __name__ == "__main__":
    unittest.main()
