"""Adding an event to an existing path (/api/branch/append) instead of starting a new branch.

The event takes effect at the chosen point; every later month of that path is simulated again with it,
keeping the events further along. Branches that split off later see the parts of the event that had
started by then; branches that split off at the chosen point itself are alternatives and stay unchanged.
"""

from copy import deepcopy
import json
import unittest

from financial_twin import simulate
from financial_twin.reproduce import build_run, verify
from financial_twin.server import Handler, append_event, apply_branch, edit_nodes
from tests.test_building_blocks import change, parent_node, proposal

CAR = {"label": "Car", "kind": "vehicle", "price_chf": 20000, "loan_chf": 15000, "down_payment_chf": 5000,
       "transaction_costs_chf": 500, "annual_interest_rate": 0.04, "monthly_payment_chf": 400,
       "annual_value_change": -0.1, "source": "user"}


def tree():
    """root ─ main (30 months); B splits off at main month 6; C splits off B at its 9th point; Alt splits off B at its 4th."""
    root = parent_node(month=0)
    main = [root, *simulate(root, 30)]
    b = apply_branch({"parent": main[6], "event": "B", "months": 20, "sequence": 0,
                      "proposal": proposal(title="B", changes=[change("expenses", 100)])})
    c = apply_branch({"parent": b["nodes"][8], "event": "C", "months": 6, "sequence": 1,
                      "proposal": proposal(title="C", changes=[change("income", 300)])})
    alt = apply_branch({"parent": b["nodes"][3], "event": "Alt", "months": 4, "sequence": 2, "proposal": proposal(title="Alt")})
    return {"main": main, "B": b["nodes"], "C": c["nodes"], "Alt": alt["nodes"],
            "records": [b["branch"], c["branch"], alt["branch"]]}


def everything(t):
    return t["main"] + t["B"] + t["C"] + t["Alt"]


def income(node):
    return node["state"]["cash_flow"]["income_chf"]


class AppendEventTests(unittest.TestCase):
    def setUp(self):
        self.t = tree()
        self.point = self.t["B"][3]

    def append(self, nodes=None, point=None, plan=None, **extra):
        plan = plan or proposal(title="Raise", changes=[change("income", 500)], assets=[CAR])
        return append_event({"nodes": nodes or everything(self.t), "node_id": (point or self.point)["id"],
                             "event": "Raise and car", "proposal": plan, "sequence": 3, **extra})

    def test_the_rest_of_the_path_is_recalculated_with_the_event(self):
        result = self.append()
        new = {node["id"]: node for node in result["nodes"]}
        b, c = self.t["B"], self.t["C"]
        self.assertEqual(set(new), {node["id"] for node in b[4:] + c}, "same IDs: the path changes, no new branch")
        first = new[b[4]["id"]]
        self.assertEqual(first["event_summary"], "Raise and car")
        self.assertEqual(income(first), income(b[4]) + 500)
        self.assertEqual(income(new[b[-1]["id"]]), income(b[-1]) + 500)
        self.assertIn(f"event-debt-{result['appended']['id']}-0", [debt["id"] for debt in first["state"]["debts"]])
        self.assertEqual(first["state"]["cash_flow"]["one_off_costs_chf"], 5500, "down payment and purchase costs")
        self.assertEqual(first["appended_events"][0]["title"], "Raise and car")

    def test_earlier_points_and_alternatives_at_the_same_point_stay_unchanged(self):
        changed = {node["id"] for node in self.append()["nodes"]}
        for node in self.t["main"] + self.t["B"][:4] + self.t["Alt"]:
            self.assertNotIn(node["id"], changed)

    def test_later_branches_keep_their_own_event_on_top(self):
        new = {node["id"]: node for node in self.append()["nodes"]}
        for old in self.t["C"]:
            self.assertEqual(income(new[old["id"]]), income(old) + 500)

    def test_later_branches_only_see_what_had_started_when_they_split_off(self):
        # C splits off at B's 9th point (month 14). A change from month 15 on and a one-off at month 16 are
        # plans the original path made after C replaced its future, so C does not get them.
        late = proposal(title="Later", changes=[change("income", 700, offset=6)],
                        one_offs=[{"label": "Trip", "amount_chf": 900, "offset_months": 7, "source": "user"}])
        new = {node["id"]: node for node in self.append(plan=late)["nodes"]}
        self.assertEqual(income(new[self.t["B"][-1]["id"]]), income(self.t["B"][-1]) + 700)
        for old in self.t["C"]:
            for field in ("cash_chf", "net_worth_chf", "total_assets_chf", "total_debt_chf"):
                self.assertAlmostEqual(new[old["id"]]["state"][field], old["state"][field], places=6)
            self.assertEqual(income(new[old["id"]]), income(old))

    def test_accounting_holds_on_every_recalculated_month(self):
        result = self.append()
        new = {node["id"]: node for node in result["nodes"]}
        everything_now = {node["id"]: node for node in everything(self.t)} | new
        for node in result["nodes"]:
            parent = everything_now[node["parent_id"]]
            state = node["state"]
            self.assertAlmostEqual(parent["state"]["cash_chf"] + state["cash_flow"]["net_cash_flow_chf"], state["cash_chf"], places=6)
            self.assertAlmostEqual(state["total_assets_chf"] - state["total_debt_chf"], state["net_worth_chf"], places=6)
            self.assertEqual(node["month"], parent["month"] + 1)

    def test_the_last_point_of_a_path_extends_it(self):
        last = self.t["Alt"][-1]
        result = self.append(point=last, months=5)
        self.assertEqual(len(result["nodes"]), 5)
        self.assertTrue(all(node["branch_id"] == last["branch_id"] for node in result["nodes"]))
        self.assertEqual(result["nodes"][0]["parent_id"], last["id"])
        self.assertEqual(result["record"]["months"], 5)
        with self.assertRaisesRegex(ValueError, "last point"):
            self.append(point=last)

    def test_two_events_at_the_same_point_add_up(self):
        first = self.append()
        nodes = {node["id"]: node for node in everything(self.t)} | {node["id"]: node for node in first["nodes"]}
        second = append_event({"nodes": list(nodes.values()), "node_id": self.point["id"], "event": "Bonus",
                               "proposal": proposal(title="Bonus", changes=[change("income", 200)]), "sequence": 4})
        start = second["nodes"][0]
        self.assertEqual(start["event_summary"], "Raise and car · Bonus")
        self.assertEqual([item["title"] for item in start["appended_events"]], ["Raise and car", "Bonus"])
        self.assertEqual(income(start), income(self.t["B"][4]) + 700)
        self.assertEqual(sum(debt["id"].startswith("event-debt-a") for debt in start["state"]["debts"]), 1, "the car loan once")

    def test_an_event_added_earlier_keeps_one_added_later(self):
        later = append_event({"nodes": everything(self.t), "node_id": self.t["B"][10]["id"], "event": "Car",
                              "proposal": proposal(title="Car", assets=[CAR]), "sequence": 3})
        nodes = {node["id"]: node for node in everything(self.t)} | {node["id"]: node for node in later["nodes"]}
        earlier = append_event({"nodes": list(nodes.values()), "node_id": self.point["id"], "event": "Raise",
                                "proposal": proposal(title="Raise", changes=[change("income", 500)]), "sequence": 4})
        new = {node["id"]: node for node in earlier["nodes"]}
        car_point = new[self.t["B"][11]["id"]]
        self.assertEqual(car_point["event_summary"], "Car")
        self.assertTrue(any(asset.get("name") == "Car" for asset in car_point["state"]["assets"]))
        self.assertEqual(income(car_point), income(self.t["B"][11]) + 500)

    def test_editing_the_start_later_keeps_the_added_event(self):
        result = self.append()
        nodes = {node["id"]: node for node in everything(self.t)} | {node["id"]: node for node in result["nodes"]}
        root = self.t["main"][0]
        edited = {node["id"]: node for node in edit_nodes({"nodes": list(nodes.values()), "node_id": root["id"],
                                                           "edits": {"cash_chf": 50000}})["nodes"]}
        start = edited[self.t["B"][4]["id"]]
        self.assertEqual(start["event_summary"], "Raise and car")
        self.assertEqual(income(start), income(self.t["B"][4]) + 500)
        self.assertTrue(any(debt["id"].startswith("event-debt-a") for debt in start["state"]["debts"]))

    def test_run_files_replay_added_events_exactly(self):
        t = self.t
        result = self.append()
        nodes = {node["id"]: node for node in everything(t)} | {node["id"]: node for node in result["nodes"]}
        run = build_run(source="simulate", years=0, scenario=None, branches=[*t["records"], result["record"]], nodes=list(nodes.values()))
        run["source"] = "test"   # replay the main path from the same root instead of a scenario
        self.assertEqual(result["record"]["kind"], "append")

        # Replay needs a main path it can rebuild: use the run's own nodes through a custom replay.
        from financial_twin import reproduce
        original_random_test = reproduce.random_test
        reproduce.random_test = lambda data: {"nodes": deepcopy(t["main"])}
        try:
            report = verify(json.loads(json.dumps(run)))
            self.assertTrue(report["ok"], report["mismatches"][:3] or report["missing_nodes"][:3])
            # After cutting a path and extending its new end, replay still has the cut points; it extends too.
            alt_kept = t["Alt"][:2]
            extended = append_event({"nodes": t["main"] + t["B"] + t["C"] + alt_kept, "node_id": alt_kept[-1]["id"],
                                     "event": "Extend", "proposal": proposal(title="Extend", changes=[change("income", 50)]),
                                     "sequence": 3, "months": 4})
            kept = t["main"] + t["B"] + t["C"] + alt_kept + extended["nodes"]
            cut = build_run(source="test", years=0, branches=[*t["records"], extended["record"]], nodes=kept)
            self.assertTrue(verify(cut)["ok"])
        finally:
            reproduce.random_test = original_random_test

    def test_run_operations_replay_branches_added_events_and_edits_in_order(self):
        t = self.t
        added = self.append()
        nodes = {node["id"]: node for node in everything(t)} | {node["id"]: node for node in added["nodes"]}
        edits = {"cash_chf": 25000}
        edited = edit_nodes({"nodes": list(nodes.values()), "node_id": t["main"][0]["id"], "edits": edits})
        nodes |= {node["id"]: node for node in edited["nodes"]}
        run = build_run(source="test", years=0, branches=t["records"], nodes=list(nodes.values()))
        run["operations"] = [*({"type": "branch", "branch": record} for record in t["records"]),
                             {"type": "append", "append": added["record"]},
                             {"type": "edit", "node_id": t["main"][0]["id"], "edits": edits}]
        from financial_twin import reproduce
        original_random_test = reproduce.random_test
        reproduce.random_test = lambda data: {"nodes": deepcopy(t["main"])}
        try:
            report = verify(json.loads(json.dumps(run)))
        finally:
            reproduce.random_test = original_random_test
        self.assertTrue(report["ok"], report["mismatches"][:3] or report["missing_nodes"][:3])

    def test_bad_requests_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Choose a point"):
            append_event({"nodes": everything(self.t), "node_id": "nope", "proposal": proposal()})
        broken = everything(self.t)[:5] + self.t["C"]
        with self.assertRaisesRegex(ValueError, "Invalid timeline structure"):
            append_event({"nodes": broken, "node_id": self.t["main"][2]["id"], "proposal": proposal()})
        with self.assertRaises(ValueError):
            self.append(sequence=-1)

    def test_through_the_http_api(self):
        from http.server import ThreadingHTTPServer
        import threading
        from urllib.error import HTTPError
        from urllib.request import Request, urlopen
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        httpd.RequestHandlerClass.log_message = lambda *args: None
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)

        def post(body):
            request = Request(f"http://127.0.0.1:{httpd.server_port}/api/branch/append", data=json.dumps(body).encode(),
                              headers={"Content-Type": "application/json"})
            try:
                with urlopen(request) as response:
                    return response.status, json.loads(response.read())
            except HTTPError as error:
                with error:
                    return error.code, json.loads(error.read())

        status, data = post({"nodes": everything(self.t), "node_id": self.point["id"], "event": "Raise",
                             "proposal": proposal(title="Raise", changes=[change("income", 500)]), "sequence": 3})
        self.assertEqual(status, 200)
        self.assertEqual(data["record"]["kind"], "append")
        self.assertEqual(len(data["nodes"]), len(self.t["B"]) - 4 + len(self.t["C"]))
        status, data = post({"nodes": everything(self.t), "node_id": "nope", "proposal": proposal()})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
