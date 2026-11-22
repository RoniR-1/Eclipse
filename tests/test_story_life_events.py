from datetime import date
import unittest
from unittest.mock import patch

from financial_twin import simulate
from financial_twin.reproduce import build_run, verify
from financial_twin.server import append_event, apply_branch, extract, propose_branch, run_simulation
from tests.test_building_blocks import change, parent_node, proposal
from tests import test_web


class FixedDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 1, 1)


class StoryLifeEventTests(unittest.TestCase):
    def setUp(self):
        self.cache = patch("financial_twin.server.cached_ai_answer", return_value=None)
        self.cache.start()
        self.addCleanup(self.cache.stop)
        self.store = patch("financial_twin.server.store_ai_answer", side_effect=lambda kind, prompt, schema, answer: answer)
        self.store.start()
        self.addCleanup(self.store.stop)

    def test_bachelor_story_keeps_work_and_adds_three_year_phase(self):
        answer = test_web.WebFlowTests().gemini_result()
        answer["phases"] = [{"name": "Work", "start_month": 0, "monthly_income_chf": 4000,
                             "monthly_expenses_chf": 2000}]
        answer["one_off_costs"] = []
        story = "i wanna work and do a 3 year bachelors in 2030 and then continue working"
        with patch("financial_twin.server.date", FixedDate), patch("financial_twin.server.gemini_json", return_value=answer):
            result = extract(story, 10, "key")
        phases = result["scenario"]["phases"]
        study = next(phase for phase in phases if "bachelor" in phase["name"].lower())
        self.assertEqual((study["start_month"], study["end_month"]), (48, 84))
        self.assertEqual(study["monthly_income_chf"], 4000)
        self.assertEqual(phases[-1]["monthly_income_chf"], 4000)

    def test_bachelor_proposal_applies_in_branch_and_append(self):
        root = parent_node(month=0)
        main = [root, *simulate(root, 96)]
        parent = main[12]
        story = "i wanna work and do a 3 year bachelors in 2030 and then continue working"
        with patch("financial_twin.server.gemini_json", return_value=proposal(title="Work")):
            plan = propose_branch({"parent": parent, "event": story}, "key")["proposal"]
        study = next(change for change in plan["changes"] if "bachelor" in change["label"].lower())
        self.assertEqual((study["start_offset_months"], study["duration_months"]), (36, 36))
        branch = apply_branch({"parent": parent, "event": story, "months": 84, "proposal": plan})["nodes"]
        appended = append_event({"nodes": main, "node_id": parent["id"], "event": story, "proposal": plan})["nodes"]
        for nodes in (branch, appended):
            self.assertEqual(nodes[0]["state"]["cash_flow"]["income_chf"], 4000)
            self.assertGreater(nodes[36]["state"]["cash_flow"]["expenses_chf"], nodes[35]["state"]["cash_flow"]["expenses_chf"])
            self.assertEqual(nodes[72]["state"]["cash_flow"]["income_chf"], 4000)
            self.assertEqual(nodes[72]["state"]["cash_flow"]["expenses_chf"], nodes[35]["state"]["cash_flow"]["expenses_chf"])

    def test_sabbatical_story_creates_separate_alternatives(self):
        answer = test_web.WebFlowTests().gemini_result()
        answer["opening_cash_chf"] = 32000
        answer["debts"] = [{"id": "student-loan", "principal_chf": 12000,
                            "annual_interest_rate": 0.03, "monthly_payment_chf": 350}]
        answer["phases"] = [{"name": "Work", "start_month": 0, "monthly_income_chf": 6200,
                             "monthly_expenses_chf": 3900}]
        answer["one_off_costs"] = []
        options = {"alternatives": [
            {"description": "Take a 1-year sabbatical", "proposal": proposal(
                title="Sabbatical", changes=[change("income", -6200, 0, 12, "Sabbatical")])},
            {"description": "Go part-time", "proposal": proposal(
                title="Part-time", changes=[change("income", -3100, 0, 0, "Part-time")])},
        ]}
        story = ("I have CHF 32,000 in savings, CHF 12,000 left on a student loan at 3% paying "
                 "CHF 350/month, earn CHF 6,200 net and spend CHF 3,900 monthly. "
                 "I'm thinking about taking a 1-year sabbatical or going part-time.")
        with patch("financial_twin.server.date", FixedDate), patch("financial_twin.server.gemini_json", side_effect=[answer, options]):
            result = extract(story, 5, "key")
        self.assertEqual(result["scenario"]["phases"][0]["monthly_income_chf"], 6200)
        self.assertEqual([option["title"] for option in result["alternatives"]], ["Sabbatical", "Part-time"])
        debt = result["scenario"]["debts"][0]
        self.assertEqual((debt["principal_chf"], debt["annual_interest_rate"], debt["monthly_payment_chf"]),
                         (12000, 0.03, 350))
        variables = {item["id"]: item["value"] for item in result["variables"]}
        simulation = run_simulation({"scenario": result["scenario"], "variables": variables,
                                     "years": 2, "alternatives": result["alternatives"]})
        self.assertEqual(len(simulation["branches"]), 2)
        baseline = next(node for node in simulation["nodes"] if node["id"] == simulation["baseline_leaf_id"])
        self.assertEqual(baseline["branch_id"], "main")
        starts = [node for node in simulation["nodes"] if node["parent_id"] == simulation["nodes"][0]["id"]
                  and node["branch_id"] != "main"]
        paths = [[node for node in simulation["nodes"] if node["branch_id"] == start["branch_id"]]
                 for start in starts]
        self.assertEqual(paths[0][0]["state"]["cash_flow"]["income_chf"], 0)
        self.assertEqual(paths[0][12]["state"]["cash_flow"]["income_chf"], 6200)
        self.assertEqual(paths[1][0]["state"]["cash_flow"]["income_chf"], 3100)
        self.assertEqual(paths[1][12]["state"]["cash_flow"]["income_chf"], 3100)
        run = build_run(source="simulate", years=2, scenario=simulation["scenario"], variables=variables,
                        branches=simulation["branches"], nodes=simulation["nodes"])
        report = verify(run)
        self.assertTrue(report["ok"], report["mismatches"][:3])

    def test_empty_alternatives_keeps_baseline(self):
        answer = test_web.WebFlowTests().gemini_result()
        with patch("financial_twin.server.date", FixedDate), patch("financial_twin.server.gemini_json", side_effect=[answer, {"alternatives": []}]):
            result = extract("I work in marketing or sales", 2, "key")
        self.assertEqual(result["alternatives"], [])
        variables = {item["id"]: item["value"] for item in result["variables"]}
        simulation = run_simulation({"scenario": result["scenario"], "variables": variables,
                                     "years": 2, "alternatives": result["alternatives"]})
        self.assertEqual(simulation["branches"], [])
        self.assertEqual(len(simulation["nodes"]), 25)

    def test_single_sabbatical_story_restores_income(self):
        answer = test_web.WebFlowTests().gemini_result()
        answer["phases"] = [{"name": "Work", "start_month": 0, "monthly_income_chf": 6200,
                             "monthly_expenses_chf": 3900}]
        answer["one_off_costs"] = []
        with patch("financial_twin.server.date", FixedDate), patch("financial_twin.server.gemini_json", return_value=answer):
            result = extract("Take a 1-year sabbatical and then continue working", 5, "key")
        phases = result["scenario"]["phases"]
        leave = next(phase for phase in phases if "sabbatical" in phase["name"].lower())
        self.assertEqual(leave["end_month"] - leave["start_month"], 12)
        self.assertEqual(leave["monthly_income_chf"], 0)
        self.assertEqual(phases[-1]["monthly_income_chf"], 6200)

    def test_sabbatical_proposal_applies_in_branch_and_append(self):
        root = parent_node(month=0)
        main = [root, *simulate(root, 36)]
        parent = main[12]
        story = "Take a 1-year sabbatical and then continue working"
        with patch("financial_twin.server.gemini_json", return_value=proposal(title="Work")):
            plan = propose_branch({"parent": parent, "event": story}, "key")["proposal"]
        branch = apply_branch({"parent": parent, "event": story, "months": 24, "proposal": plan})["nodes"]
        appended = append_event({"nodes": main, "node_id": parent["id"], "event": story, "proposal": plan})["nodes"]
        for nodes in (branch, appended):
            self.assertEqual([node["state"]["cash_flow"]["income_chf"] for node in nodes[:12]], [0] * 12)
            self.assertEqual(nodes[12]["state"]["cash_flow"]["income_chf"], 4000)

    def test_existing_bachelor_change_uses_requested_period_and_preserves_work(self):
        parent = parent_node(month=12)
        story = "i wanna work and do a 3 year bachelors in 2030 and then continue working"
        answer = proposal(title="Study", changes=[change("expenses", 100, 0, 24, "Tuition"),
                                                     change("income", -2000, 0, 24, "Study income")])
        with patch("financial_twin.server.gemini_json", return_value=answer):
            plan = propose_branch({"parent": parent, "event": story}, "key")["proposal"]
        tuition = next(item for item in plan["changes"] if item["label"] == "Tuition")
        self.assertEqual((tuition["start_offset_months"], tuition["duration_months"]), (36, 36))
        self.assertFalse(any(item["kind"] == "income" for item in plan["changes"]))

    def test_part_time_without_percentage_discloses_assumption(self):
        parent = parent_node(month=0)
        with patch("financial_twin.server.gemini_json", return_value=proposal(title="Work")):
            plan = propose_branch({"parent": parent, "event": "Go part-time"}, "key")["proposal"]
        nodes = apply_branch({"parent": parent, "event": "Go part-time", "months": 2, "proposal": plan})["nodes"]
        self.assertEqual(nodes[0]["state"]["cash_flow"]["income_chf"], 2400)
        self.assertIn("60%", plan["assumptions"])
