"""Building-block branches: proposals, durations, purchase accounting, AI-filled event types and replay records."""
import json
from pathlib import Path
import random
import unittest
from unittest.mock import patch

from financial_twin import create_root, simulate
from financial_twin.branching import PRESETS, SAMPLES, apply_proposal, normalize_proposal, sample_proposal
from financial_twin.server import VARIABLES, apply_branch, propose_branch, random_test, run_simulation


def parent_node(cash=10000, investments=20000, month=12):
    scenario = {
        "start_date": "2026-01-01", "opening_cash_chf": cash,
        "phases": [{"name": "Work", "start_month": 0, "end_month": None,
                    "monthly_income_chf": 4000, "monthly_expenses_chf": 2000}],
        "assets": [{"id": "etf", "type": "investment", "value_chf": investments,
                    "annual_return": 0, "annual_volatility": 0}],
        "debts": [],
    }
    root = create_root(scenario)
    return simulate(root, month)[-1] if month else root


def proposal(**blocks):
    base = {"title": "Event", "summary": "", "assumptions": "", "changes": [], "one_offs": [],
            "assets": [], "investment_multiplier": 1}
    base.update(blocks)
    return base


def branch(parent, plan, months=24):
    return apply_proposal(parent, plan["title"], months, {}, plan, VARIABLES)["nodes"]


def change(kind, amount, offset=0, duration=0, label="Change"):
    return {"kind": kind, "label": label, "amount_chf": amount, "start_offset_months": offset,
            "duration_months": duration, "source": "user"}


class BranchingTests(unittest.TestCase):
    def test_temporary_change_ends_and_later_level_starts(self):
        parent = parent_node()
        nodes = branch(parent, proposal(changes=[change("income", -3000, 0, 6, "Studying"),
                                                 change("income", 1000, 6, 0, "Graduate salary")]))
        incomes = [node["state"]["cash_flow"]["income_chf"] for node in nodes[:8]]
        self.assertEqual(incomes, [1000] * 6 + [5000] * 2)
        self.assertEqual(nodes[0]["event_summary"], "Event")
        self.assertIn("Graduate salary", nodes[6]["event_summary"])

    def test_purchase_enforces_price_equals_loan_plus_down_payment(self):
        parent = parent_node(cash=200000)
        asset = {"label": "Flat", "kind": "property", "price_chf": 600000, "loan_chf": 480000,
                 "down_payment_chf": 60000, "transaction_costs_chf": 12000, "annual_interest_rate": 0,
                 "monthly_payment_chf": 0, "annual_value_change": 0, "source": "ai_estimate"}
        plan = normalize_proposal(proposal(assets=[asset]), parent)
        self.assertEqual(plan["assets"][0]["down_payment_chf"], 120000)
        self.assertTrue(any("down payment" in warning for warning in plan["warnings"]))
        bought = branch(parent, proposal(assets=[asset]), 1)[0]["state"]
        baseline = branch(parent, proposal(), 1)[0]["state"]
        # Buying moves cash into the flat; only the transaction costs reduce net worth.
        self.assertAlmostEqual(bought["net_worth_chf"] - baseline["net_worth_chf"], -12000)
        self.assertAlmostEqual(bought["cash_chf"] - baseline["cash_chf"], -132000)
        self.assertAlmostEqual(bought["total_debt_chf"], 480000)

    def test_money_received_is_one_month_of_income(self):
        parent = parent_node()
        gift = {"label": "Gift", "amount_chf": -20000, "offset_months": 2, "source": "user"}
        nodes = branch(parent, proposal(one_offs=[gift]), 6)
        base = branch(parent, proposal(), 6)
        self.assertEqual(nodes[2]["state"]["cash_flow"]["income_chf"], 24000)
        self.assertEqual(nodes[3]["state"]["cash_flow"]["income_chf"], 4000)
        self.assertAlmostEqual(nodes[-1]["state"]["cash_chf"] - base[-1]["state"]["cash_chf"], 20000)

    def test_warnings_for_upfront_costs_and_growing_loans(self):
        parent = parent_node(cash=5000, month=0)
        car ={"label": "Car", "kind": "vehicle", "price_chf": 30000, "loan_chf": 20000, "down_payment_chf": 10000,
               "transaction_costs_chf": 0, "annual_interest_rate": 0.06, "monthly_payment_chf": 50,
               "annual_value_change": -0.15, "source": "user"}
        warnings = normalize_proposal(proposal(assets=[car]), parent)["warnings"]
        self.assertTrue(any("funding gap" in warning for warning in warnings))
        self.assertTrue(any("loan balance grows" in warning for warning in warnings))

    def test_every_sample_applies_and_keeps_cash_reconciled(self):
        parent = parent_node(cash=150000)
        for kind in SAMPLES:
            with self.subTest(kind=kind):
                plan = sample_proposal(parent, kind)
                nodes = branch(parent, plan, 36)
                previous = parent
                for node in nodes:
                    flow = node["state"]["cash_flow"]["net_cash_flow_chf"]
                    self.assertAlmostEqual(previous["state"]["cash_chf"] + flow, node["state"]["cash_chf"], places=6)
                    previous = node

    def test_property_purchase_creates_no_free_equity(self):
        parent = parent_node(cash=150000, investments=0)
        plan = sample_proposal(parent, "buy_property")
        home = plan["assets"][0]
        self.assertEqual(home["price_chf"], home["loan_chf"] + home["down_payment_chf"])
        first = branch(parent, plan, 1)[0]
        base = branch(parent, proposal(), 1)[0]
        interest = home["loan_chf"] * home["annual_interest_rate"] / 12
        growth = home["price_chf"] * ((1 + home["annual_value_change"]) ** (1 / 12) - 1)
        maintenance = plan["changes"][0]["amount_chf"]
        expected = -home["transaction_costs_chf"] - interest + growth - maintenance
        self.assertAlmostEqual(first["state"]["net_worth_chf"] - base["state"]["net_worth_chf"], expected, places=6)

    def test_invalid_blocks_are_rejected(self):
        bad = [
            proposal(changes=[change("bonus", 100)]),
            proposal(changes=[change("income", 100, 1.5)]),
            proposal(changes=[change("income", 100)] * 9),
            proposal(investment_multiplier=-1),
            proposal(assets=[{"label": "X", "price_chf": -5}]),
        ]
        for plan in bad:
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                normalize_proposal(plan)

    def test_presets_and_text_are_filled_by_the_ai_and_apply_never_calls_it(self):
        parent = parent_node()
        ai = proposal(title="Master's at ETH", summary="Two years of study.",
                      changes=[change("expenses", 120, 0, 24), change("income", -3200, 0, 24)])
        ai["source_urls"] = ["https://ethz.ch/fees", "not a url", "https://ethz.ch/fees"]
        with patch("financial_twin.server.gemini_json", return_value=ai) as gemini:
            result = propose_branch({"parent": parent, "preset": "education", "event": "Master's at ETH Zurich"}, "key")
        prompt = gemini.call_args.args[0]
        self.assertTrue(gemini.call_args.kwargs.get("web_search"))
        self.assertIn(PRESETS["education"]["guidance"], prompt)
        self.assertIn("Master's at ETH Zurich", prompt)
        self.assertEqual(result["origin"], "ai")
        self.assertEqual(result["proposal"]["source_urls"], ["https://ethz.ch/fees"])
        with patch("financial_twin.server.gemini_json", return_value=ai) as gemini:
            propose_branch({"parent": parent, "preset": "market_shock"}, "key")
        self.assertIn("No details given", gemini.call_args.args[0])
        with patch("financial_twin.server.gemini_json") as gemini:
            applied = apply_branch({"parent": parent, "event": "Study", "months": 30, "proposal": result["proposal"]})
        gemini.assert_not_called()
        self.assertEqual(applied["nodes"][24]["state"]["cash_flow"]["income_chf"], 4000)

    def test_falls_back_without_web_search_when_search_fails(self):
        ai = proposal(title="Repair", one_offs=[{"label": "Repair", "amount_chf": 900, "offset_months": 0, "source": "ai_estimate"}])
        with patch("financial_twin.server.gemini_json", side_effect=[ValueError("search rejected"), ai]) as gemini:
            result = propose_branch({"parent": parent_node(), "preset": "unexpected_expense"}, "key")
        self.assertEqual([call.kwargs.get("web_search", False) for call in gemini.call_args_list], [True, False])
        self.assertEqual(result["proposal"]["one_offs"][0]["amount_chf"], 900)

    def test_propose_needs_a_known_type_or_a_description(self):
        for body in ({}, {"event": "   "}, {"preset": "lottery"}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                propose_branch({"parent": parent_node(), **body}, "key")

    def test_invalid_ai_output_gives_a_readable_error(self):
        with patch("financial_twin.server.gemini_json", return_value={"changes": "nope"}):
            with self.assertRaisesRegex(ValueError, "rephrase"):
                propose_branch({"parent": parent_node(), "event": "Something"}, "key")

    def test_random_test_scenarios_reconcile(self):
        for seed in range(20):
            nodes = {node["id"]: node for node in random_test({"years": 10}, random.Random(seed))["nodes"]}
            for node in nodes.values():
                parent = nodes.get(node["parent_id"])
                if parent:
                    flow = node["state"]["cash_flow"]["net_cash_flow_chf"]
                    self.assertAlmostEqual(parent["state"]["cash_chf"] + flow, node["state"]["cash_chf"], places=6)


class ReproducibleBranchTests(unittest.TestCase):
    """Building-block branches take part in the run file and replay, like one-step branches."""

    def plan(self):
        return proposal(title="Sabbatical", changes=[change("income", -4000, 0, 6, "Unpaid leave")],
                        one_offs=[{"label": "Trip", "amount_chf": 8000, "offset_months": 0, "source": "user"}])

    def test_sequence_makes_branch_ids_deterministic_and_records_the_branch(self):
        parent = parent_node()
        first = apply_branch({"parent": parent, "event": "Sabbatical", "months": 12, "proposal": self.plan(), "sequence": 0})
        again = apply_branch({"parent": parent, "event": "Sabbatical", "months": 12, "proposal": self.plan(), "sequence": 0})
        other = apply_branch({"parent": parent, "event": "Sabbatical", "months": 12, "proposal": self.plan(), "sequence": 1})
        self.assertEqual([n["id"] for n in first["nodes"]], [n["id"] for n in again["nodes"]])
        self.assertNotEqual(first["nodes"][0]["branch_id"], other["nodes"][0]["branch_id"])
        record = first["branch"]
        self.assertEqual((record["kind"], record["sequence"], record["parent_id"]), ("proposal", 0, parent["id"]))
        self.assertNotIn("warnings", record["proposal"])
        unsequenced = apply_branch({"parent": parent, "event": "Sabbatical", "months": 12, "proposal": self.plan()})
        self.assertIsNone(unsequenced["branch"]["sequence"])
        with self.assertRaises(ValueError):
            apply_branch({"parent": parent, "event": "Sabbatical", "months": 12, "proposal": self.plan(), "sequence": -1})

    def test_a_run_with_a_building_block_branch_replays_exactly(self):
        from financial_twin.reproduce import build_run, verify
        main = run_simulation({"scenario": {"start_date": "2026-01-01", "opening_cash_chf": 10000, "annual_inflation": 0.02,
                                            "phases": [{"start_month": 0, "end_month": None, "monthly_income_chf": 5000,
                                                        "monthly_expenses_chf": 3000}],
                                            "one_off_costs": [], "assets": [], "debts": []},
                               "years": 2, "variables": {"opening_cash_chf": 10000, "monthly_income_chf": 5000,
                                                         "monthly_expenses_chf": 3000}})
        nodes = main["nodes"]
        branch = apply_branch({"parent": nodes[6], "event": "Sabbatical", "months": 18, "proposal": self.plan(), "sequence": 0})
        run = build_run(source="simulate", years=2, scenario=main["scenario"], branches=[branch["branch"]],
                        variables={"opening_cash_chf": 10000, "monthly_income_chf": 5000, "monthly_expenses_chf": 3000},
                        nodes=nodes + branch["nodes"])
        report = verify(json.loads(json.dumps(run)))
        self.assertTrue(report["ok"], report["mismatches"][:3])
        self.assertEqual(report["checked_nodes"], len(nodes) + len(branch["nodes"]))

    def test_the_same_proposal_request_is_answered_from_the_ai_cache(self):
        import tempfile
        from financial_twin import server
        ai = self.plan()
        with tempfile.TemporaryDirectory() as cache, patch.object(server, "AI_CACHE_DIR", Path(cache)),                 patch("financial_twin.server.gemini_json", return_value=ai) as gemini:
            request = {"parent": parent_node(), "preset": "career_change", "event": "Six months off to travel"}
            first = propose_branch(request, "")
            second = propose_branch(request, "")
        self.assertEqual(gemini.call_count, 1)
        self.assertEqual((first["cached"], second["cached"]), (False, True))
        self.assertEqual(first["proposal"], second["proposal"])


class BuildingBlocksAndTimelineRulesTests(unittest.TestCase):
    """The timeline rules of the one-step branch also hold for building-block branches."""

    def test_branch_excludes_events_the_parent_planned_after_the_fork(self):
        scenario = {"start_date": "2025-01-01", "opening_cash_chf": 10000, "assets": [], "debts": [],
                    "phases": [{"name": "B", "start_month": 0, "end_month": 3, "monthly_income_chf": 4000, "monthly_expenses_chf": 2000},
                               {"name": "C", "start_month": 3, "end_month": None, "monthly_income_chf": 6000, "monthly_expenses_chf": 2000}],
                    "one_off_costs": [{"description": "C cost", "month": 3, "amount_chf": 1000}]}
        original = simulate(create_root(scenario), 5)
        raise_ = proposal(title="D", changes=[change("income", 500)])
        branch = apply_branch({"parent": original[1], "event": "D", "months": 3, "proposal": raise_})["nodes"]
        boundary = apply_branch({"parent": original[2], "event": "D", "months": 2, "proposal": raise_})["nodes"]
        self.assertEqual(branch[0]["event_summary"], "D")
        self.assertEqual(branch[1]["state"]["cash_flow"]["income_chf"], 4500)
        self.assertEqual(branch[1]["state"]["cash_flow"]["one_off_costs_chf"], 0)
        self.assertEqual(boundary[0]["state"]["cash_flow"]["income_chf"], 4500)

    def test_nested_investment_shocks_leave_parent_and_siblings_untouched(self):
        parent = parent_node(investments=10000)
        frozen = json.dumps(parent, sort_keys=True)
        crash = proposal(title="Market crash", investment_multiplier=0.7)
        first = apply_branch({"parent": parent, "event": "Market crash", "months": 2, "proposal": crash})["nodes"]
        sibling = apply_branch({"parent": parent, "event": "Market crash", "months": 2, "proposal": crash})["nodes"]
        nested = apply_branch({"parent": first[0], "event": "Another crash", "months": 1, "proposal": crash})["nodes"]
        self.assertEqual(json.dumps(parent, sort_keys=True), frozen)
        self.assertEqual((first[0]["parent_id"], sibling[0]["parent_id"], nested[0]["parent_id"]),
                         (parent["id"], parent["id"], first[0]["id"]))
        self.assertNotEqual(first[0]["id"], sibling[0]["id"])
        self.assertAlmostEqual(first[0]["state"]["risk_exposure"]["investment_value_chf"], 7000)
        self.assertAlmostEqual(nested[0]["state"]["risk_exposure"]["investment_value_chf"], 4900)

    def test_editing_the_start_carries_through_a_branch_and_keeps_its_purchase(self):
        from financial_twin.server import edit_nodes
        scenario = {"start_date": "2025-01-01", "opening_cash_chf": 10000, "annual_inflation": 0,
                    "phases": [{"start_month": 0, "end_month": None, "monthly_income_chf": 5000, "monthly_expenses_chf": 2000}],
                    "one_off_costs": [], "assets": [], "debts": [], "preferences": {}}
        root = create_root(scenario)
        main = simulate(root, 3)
        car = {"label": "Car", "kind": "vehicle", "price_chf": 20000, "loan_chf": 20000, "down_payment_chf": 0,
               "transaction_costs_chf": 0, "annual_interest_rate": 0, "monthly_payment_chf": 0,
               "annual_value_change": 0, "source": "user"}
        plan = proposal(title="New job", changes=[change("income", 1000)], assets=[car],
                        one_offs=[{"label": "Fee", "amount_chf": 1000, "offset_months": 0, "source": "user"}])
        branch = apply_branch({"parent": main[0], "event": "New job", "months": 2, "proposal": plan})["nodes"]
        edited = edit_nodes({"nodes": [root, *main, *branch], "node_id": root["id"],
                             "edits": {"cash_chf": 12000, "monthly_income_chf": 6000}})["nodes"]
        revised = {node["id"]: node for node in edited}
        self.assertEqual(revised[main[-1]["id"]]["state"]["cash_chf"], 24000)
        self.assertEqual(revised[branch[-1]["id"]]["state"]["cash_chf"], 25000)
        self.assertEqual(revised[branch[-1]["id"]]["state"]["assets"][0]["value_chf"], 20000)


if __name__ == "__main__":
    unittest.main()
