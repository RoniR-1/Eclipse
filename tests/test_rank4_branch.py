import json
import io
import unittest
from email.message import Message
from unittest.mock import Mock

from financial_twin.engine import create_root, simulate
from financial_twin.server import Handler, apply_branch_change, edit_nodes


class BranchCommitmentsTests(unittest.TestCase):
    def scenario(self):
        return {"start_date": "2025-01-01", "opening_cash_chf": 100,
                "phases": [{"start_month": 0, "end_month": 3, "monthly_income_chf": 100, "monthly_expenses_chf": 0},
                           {"start_month": 3, "end_month": None, "monthly_income_chf": 200, "monthly_expenses_chf": 0}],
                "one_off_costs": [{"month": 4, "amount_chf": 50}],
                "expense_terms": [{"start_month": 3, "end_month": 5, "monthly_expenses_chf": 10,
                                   "phase_expenses_chf": 0, "inflation_exempt": True}],
                "inflation_by_year": {"2026": 0.02},
                "goals": [{"name": "Early", "target_chf": 200, "in_months": 1, "priority": "high"},
                          {"name": "Later", "target_chf": 400, "in_months": 4, "priority": "low"},
                          {"name": "Future", "target_chf": 200, "in_months": 20}]}

    def change(self, **updates):
        change = {"monthly_income_delta_chf": 0, "monthly_expenses_delta_chf": 0,
                  "one_off_cost_chf": 0, "investment_multiplier": 1, "new_asset_chf": 0,
                  "new_debt_chf": 0, "new_debt_annual_interest_rate": 0,
                  "new_debt_monthly_payment_chf": 0}
        change.update(updates)
        return change

    def test_fork_exposes_replaced_inherited_and_new_commitments(self):
        root = create_root(self.scenario())
        prefix = simulate(root, 2)
        parent = prefix[-1]
        original = json.dumps(parent, sort_keys=True)
        branch = apply_branch_change(parent, "Study", 5, {"savings_rate": 0.5},
                                     self.change(one_off_cost_chf=40))
        commitments = branch["branch_commitments"]
        self.assertEqual(commitments["fork_date"], "2025-03-01")
        self.assertEqual(commitments["replaced"]["one_off_costs"][0]["date"], "2025-05-01")
        self.assertEqual(commitments["replaced"]["phases"][1]["effective_date"], "2025-04-01")
        self.assertEqual(commitments["new"]["one_off_costs"][0]["amount_chf"], 40)
        self.assertEqual(commitments["inherited"]["expense_terms"][0]["effective_date"], "2025-04-01")
        self.assertEqual(commitments["inherited"]["inflation_by_year"]["2026"], 0.02)
        self.assertEqual(commitments["new"]["preferences"]["savings_rate"], 0.5)
        self.assertEqual(json.dumps(parent, sort_keys=True), original)
        outcomes = {goal["name"]: goal for goal in branch["goals"]}
        self.assertEqual(outcomes["Early"]["first_attainment_month"], 1)
        self.assertIsNone(outcomes["Future"]["met"])
        sibling = apply_branch_change(parent, "Relocation", 5, {}, self.change())
        self.assertEqual(sibling["branch_commitments"]["replaced"]["one_off_costs"][0]["amount_chf"], 50)

    def test_edit_replays_branch_metadata_and_goal_history(self):
        root = create_root(self.scenario())
        prefix = simulate(root, 2)
        branch = apply_branch_change(prefix[-1], "Study", 3, {}, self.change(one_off_cost_chf=40))
        nodes = [root, *prefix, *branch["nodes"]]
        result = edit_nodes({"nodes": nodes, "node_id": root["id"], "edits": {"cash_chf": 50}})
        by_id = {node["id"]: node for node in result["nodes"]}
        fork = by_id[branch["nodes"][0]["id"]]
        self.assertEqual(fork["branch_commitments"]["replaced"]["one_off_costs"][0]["amount_chf"], 50)
        final = by_id[branch["nodes"][-1]["id"]]
        self.assertEqual(final["state"]["goal_cash_history_chf"][0], 50)
        early = next(goal for goal in final["state"]["goals"] if goal["name"] == "Early")
        self.assertFalse(early["met"])

    def test_asset_revaluation_is_reported_and_replayed(self):
        scenario = self.scenario()
        scenario["assets"] = [{"id": "fund", "type": "investment", "value_chf": 100}]
        root = create_root(scenario)
        prefix = simulate(root, 1)
        branch = apply_branch_change(prefix[-1], "Market decline", 1, {}, self.change(investment_multiplier=0.5))
        changes = branch["branch_commitments"]["replaced"]["asset_revaluations"]
        self.assertEqual((changes[0]["before"]["value_chf"], changes[0]["after"]["value_chf"]), (100, 50))
        self.assertEqual(branch["branch_commitments"]["inherited"]["assets"], [])
        result = edit_nodes({"nodes": [root, *prefix, *branch["nodes"]], "node_id": root["id"], "edits": {"cash_chf": 50}})
        fork = next(node for node in result["nodes"] if node["id"] == branch["nodes"][0]["id"])
        self.assertEqual(fork["branch_commitments"]["replaced"]["asset_revaluations"], changes)

    def test_long_history_checkpoints_and_edit_request(self):
        scenario = {"start_date": "2025-01-01", "opening_cash_chf": 0, "minimum_reserve_chf": 1,
                    "phases": [{"start_month": 0, "end_month": None, "monthly_income_chf": 0,
                                "monthly_expenses_chf": 100}],
                    "goals": [{"name": "Goal", "target_chf": 100, "in_months": 600}]}
        root = create_root(scenario)
        branch = apply_branch_change(root, "Long path", 600, {}, self.change())
        nodes = [root, *branch["nodes"]]
        checkpoint = json.loads(json.dumps(nodes[300]))
        self.assertEqual(simulate(checkpoint, 1, branch_id=nodes[301]["branch_id"])[0]["state"], nodes[301]["state"])
        checkpoint["state"]["goal_history"] = [{"cash_chf": value} for value in checkpoint["state"].pop("goal_cash_history_chf")]
        self.assertEqual(simulate(checkpoint, 1, branch_id=nodes[301]["branch_id"])[0]["state"], nodes[301]["state"])
        payload = json.dumps({"nodes": nodes, "node_id": nodes[300]["id"], "edits": {"monthly_expenses_chf": 120}}).encode()
        self.assertGreater(len(payload), 10000000)
        self.assertLess(len(payload), 67108864)
        handler = Handler.__new__(Handler)
        handler.path = "/api/edit-nodes"
        handler.headers = Message()
        handler.headers["Content-Type"] = "application/json"
        handler.headers["Content-Length"] = str(len(payload))
        handler.rfile = io.BytesIO(payload)
        handler.send_json = Mock()
        handler.do_POST()
        status, result = handler.send_json.call_args.args
        self.assertEqual(status, 200)
        self.assertEqual(result["nodes"][-1]["month"], 600)
        self.assertEqual(len(result["nodes"][-1]["state"]["goal_cash_history_chf"]), 601)


if __name__ == "__main__":
    unittest.main()
