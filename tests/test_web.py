from copy import deepcopy
import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from financial_twin import create_root, simulate
from financial_twin.server import apply_branch, edit_nodes, extract, node_narrative, run_branch, run_simulation, run_projection, random_test, years_value


class WebFlowTests(unittest.TestCase):
    def setUp(self):
        self.cooldowns = patch.dict("financial_twin.server.MODEL_COOLDOWNS", {}, clear=True)
        self.cooldowns.start()
        self.addCleanup(self.cooldowns.stop)

    def test_edit_propagates_through_fork_and_preserves_event(self):
        scenario = {"start_date": "2025-01-01", "opening_cash_chf": 10000, "annual_inflation": 0,
                    "phases": [{"start_month": 0, "end_month": None, "monthly_income_chf": 5000, "monthly_expenses_chf": 2000}],
                    "one_off_costs": [], "assets": [], "debts": [], "preferences": {}}
        root = create_root(scenario)
        main = simulate(root, 3)
        # The asset is financed by a 0% loan: building blocks never create an asset from nothing.
        proposal = {"title": "New job", "summary": "", "assumptions": "", "investment_multiplier": 1,
                    "changes": [{"kind": "income", "label": "New job", "amount_chf": 1000, "start_offset_months": 0,
                                 "duration_months": 0, "source": "user"}],
                    "one_offs": [{"label": "Fee", "amount_chf": 1000, "offset_months": 0, "source": "user"}],
                    "assets": [{"label": "Car", "kind": "vehicle", "price_chf": 20000, "loan_chf": 20000,
                                "down_payment_chf": 0, "transaction_costs_chf": 0, "annual_interest_rate": 0,
                                "monthly_payment_chf": 0, "annual_value_change": 0, "source": "user"}]}
        branch = apply_branch({"parent": main[0], "event": "New job", "months": 2, "proposal": proposal})["nodes"]
        nodes = [root, *main, *branch]
        changed = edit_nodes({"nodes": nodes, "node_id": root["id"], "edits": {"cash_chf": 12000, "monthly_income_chf": 6000}})["nodes"]
        revised = {node["id"]: node for node in changed}
        self.assertEqual(revised[main[-1]["id"]]["state"]["cash_chf"], 24000)
        self.assertEqual(revised[branch[-1]["id"]]["state"]["cash_chf"], 25000)
        self.assertEqual(revised[branch[-1]["id"]]["state"]["assets"][0]["value_chf"], 20000)
        self.assertEqual(revised[branch[-1]["id"]]["scenario"]["opening_cash_chf"], 12000)

    def test_expense_inflation_override_and_fixed_duration(self):
        scenario = {"start_date": "2025-01-01", "opening_cash_chf": 50000, "annual_inflation": 0.12,
                    "phases": [{"start_month": 0, "end_month": None, "monthly_income_chf": 0, "monthly_expenses_chf": 1000}],
                    "one_off_costs": [], "assets": [], "debts": [], "preferences": {}}
        root = create_root(scenario)
        nodes = [root, *simulate(root, 25)]
        edits = {"monthly_expenses_chf": 1200, "inflation_exempt": True,
                 "duration_months": 14, "year_inflation": 0.24, "annual_inflation": 0.06}
        changed = edit_nodes({"nodes": nodes, "node_id": root["id"], "edits": edits})["nodes"]
        revised = {node["month"]: node for node in changed}
        self.assertEqual(revised[0]["state"]["active_variables"]["expense_terms"]["year_inflation"], 0.24)
        self.assertEqual(revised[1]["state"]["cash_flow"]["expenses_chf"], 1200)
        self.assertEqual(revised[14]["state"]["cash_flow"]["expenses_chf"], 1200)
        self.assertAlmostEqual(revised[15]["state"]["cash_flow"]["expenses_chf"],
                               1000 * 1.24 * 1.06 ** (2 / 12))
        self.assertEqual(revised[15]["state"]["active_variables"]["expense_terms"]["year"], 2026)
        self.assertIsNone(revised[15]["state"]["active_variables"]["expense_terms"]["year_inflation"])

    def test_year_inflation_override_applies_to_one_year(self):
        scenario = {"start_date": "2025-01-01", "opening_cash_chf": 100000, "annual_inflation": 0.06,
                    "phases": [{"start_month": 0, "end_month": None, "monthly_income_chf": 0, "monthly_expenses_chf": 1000}],
                    "one_off_costs": [], "assets": [], "debts": [], "preferences": {}}
        root = create_root(scenario)
        nodes = [root, *simulate(root, 25)]
        changed = edit_nodes({"nodes": nodes, "node_id": root["id"], "edits": {"year_inflation": 0.24}})["nodes"]
        revised = {node["month"]: node for node in changed}
        self.assertAlmostEqual(revised[13]["state"]["cash_flow"]["expenses_chf"], 1240)
        self.assertAlmostEqual(revised[25]["state"]["cash_flow"]["expenses_chf"], 1240 * 1.06)

    def test_projection_resolves_description_and_preserves_user_amounts(self):
        data = {"profile": {"age": 30, "cash_chf": 10000, "monthly_net_income_chf": 5000, "monthly_rent_chf": 1500},
                "scenarios": {"Study": [{"type": "education", "description": "Bachelor at ETH Zurich", "in_months": 0}]},
                "horizon_years": 1}
        cached = {"params": {"annual_tuition_chf": 1500}, "source": "database_cache", "source_urls": ["https://example.org"], "note": ""}
        with patch.dict("os.environ", {"DATABASE_URL": "postgresql://test"}), patch("financial_twin.event_resolver.resolve_event", return_value=cached) as resolver:
            result = run_projection(data)
            resolver.assert_called_once()
            self.assertEqual(result["paths"]["Study"]["events"][0]["params"]["annual_tuition_chf"]["source"], "database_cache")
            data["scenarios"]["Study"][0]["params"] = {"annual_tuition_chf": 3000}
            resolver.reset_mock()
            result = run_projection(data)
            resolver.assert_not_called()
            self.assertEqual(result["paths"]["Study"]["events"][0]["params"]["annual_tuition_chf"]["value"], 3000)

    def test_year_selection_including_zero(self):
        for years in (0, 1, 20):
            self.assertEqual(years_value(years), years)
        for years in (-1, 21, 1.5, True):
            with self.assertRaises(ValueError):
                years_value(years)
        result = random_test({"years": 0})
        self.assertEqual(result["years"], 0)
        self.assertEqual(len(result["nodes"]), 1)
        variables = {item["id"]: item["value"] for item in result["extraction"]["variables"]}
        simulation = run_simulation({"years": 0, "scenario": result["extraction"]["scenario"], "variables": variables})
        self.assertEqual(len(simulation["nodes"]), 1)
        self.assertEqual(simulation["nodes"][0]["month"], 0)

    def gemini_result(self):
        return {
            "summary": "A period of study followed by work",
            "assumptions": "Living costs stay constant",
            "opening_cash_chf": 10000,
            "annual_inflation": 0,
            "assets": [],
            "debts": [],
            "phases": [
                {"name": "Study", "start_month": 0, "monthly_income_chf": 1000, "monthly_expenses_chf": 2000},
                {"name": "Work", "start_month": 12, "monthly_income_chf": 4000, "monthly_expenses_chf": 2000},
            ],
            "one_off_costs": [{"description": "Tuition", "month": 0, "amount_chf": 500}],
            "preferences": {
                "savings_rate": 0, "risk_tolerance": 0.5, "safety_priority": 0.5,
                "emergency_months": 3, "investment_annual_return": 0.04,
                "investment_annual_volatility": 0.1,
            },
            "variable_ids": ["opening_cash_chf", "monthly_income_chf", "monthly_expenses_chf"],
        }

    def response(self, data):
        return io.BytesIO(json.dumps({"candidates": [{"content": {"parts": [{"text": json.dumps(data)}]}}]}).encode())

    def test_gemini_request_and_selected_variables(self):
        with patch("financial_twin.server.urlopen", return_value=self.response(self.gemini_result())) as urlopen:
            result = extract("I want to study and then work", 5, "test-key")
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["generationConfig"]["responseMimeType"], "application/json")
        self.assertIn("responseJsonSchema", payload["generationConfig"])
        self.assertEqual(request.get_header("X-goog-api-key"), "test-key")
        self.assertEqual([item["id"] for item in result["variables"]], self.gemini_result()["variable_ids"])
        self.assertEqual(result["scenario"]["phases"][0]["end_month"], 12)

    def test_configured_openai_endpoint_and_model_fallback(self):
        data = self.gemini_result()
        response = io.BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps(data)}}]}).encode())
        error = HTTPError("https://example.test", 404, "Unavailable", {}, None)
        environment = {"AI_BASE_URL": "https://example.test/v1/", "AI_API_KEY_MODEL": "first, second"}
        with patch.dict("os.environ", environment), patch("financial_twin.server.urlopen", side_effect=[error, response]) as urlopen:
            result = extract("Study then work", 5, "test-key")
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "https://example.test/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        self.assertEqual(payload["model"], "second")
        self.assertEqual(payload["response_format"]["json_schema"]["schema"]["type"], "object")
        self.assertEqual(result["summary"], data["summary"])

    def test_configured_model_fallback_on_transient_failures(self):
        data = self.gemini_result()
        environment = {"AI_BASE_URL": "https://example.test/v1/", "AI_API_KEY_MODEL": "first, second"}
        failures = [HTTPError("https://example.test", 503, "Unavailable", {}, None),
                    TimeoutError(), URLError(TimeoutError())]
        for failure in failures:
            response = io.BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps(data)}}]}).encode())
            with self.subTest(failure=type(failure).__name__), patch.dict("financial_twin.server.MODEL_COOLDOWNS", {}, clear=True), patch.dict("os.environ", environment), patch("financial_twin.server.urlopen", side_effect=[failure, response]) as urlopen:
                result = extract("Study then work", 5, "test-key")
            self.assertEqual(urlopen.call_count, 2)
            self.assertEqual(result["summary"], data["summary"])

    def test_model_cooldown_recovers_preferred_model(self):
        from financial_twin.server import gemini_json

        requests = []

        def send(request, timeout):
            model = json.loads(request.data)["model"]
            requests.append(model)
            if len(requests) == 1:
                raise HTTPError(request.full_url, 429, "Busy", {}, None)
            return io.BytesIO(json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode())

        environment = {"AI_BASE_URL": "https://example.test/v1", "AI_API_KEY_MODEL": "first, second", "AI_MODEL_RETRY_SECONDS": "60"}
        with patch.dict("os.environ", environment), patch("financial_twin.server.urlopen", side_effect=send), patch("financial_twin.server.monotonic") as now:
            now.return_value = 100
            gemini_json("test", {}, "key")
            now.return_value = 120
            gemini_json("test", {}, "key")
            gemini_json("test", {}, "other-key")
            now.return_value = 160
            gemini_json("test", {}, "key")
        self.assertEqual(requests, ["first", "second", "second", "first", "first"])

    def test_rejects_invalid_gemini_variable_selection(self):
        for ids in (["opening_cash_chf"] * 3, ["opening_cash_chf", "monthly_income_chf", "house_price"]):
            data = self.gemini_result()
            data["variable_ids"] = ids
            with self.subTest(ids=ids), patch("financial_twin.server.urlopen", return_value=self.response(data)):
                with self.assertRaisesRegex(ValueError, "invalid scenario"):
                    extract("Study", 5, "test-key")

    def test_slider_values_drive_timeline_and_preserve_later_phases(self):
        with patch("financial_twin.server.urlopen", return_value=self.response(self.gemini_result())):
            extracted = extract("Study then work", 5, "test-key")
        result = run_simulation({
            "scenario": extracted["scenario"], "years": 2,
            "variables": {"opening_cash_chf": 20000, "monthly_income_chf": 1500, "monthly_expenses_chf": 2000},
        })
        self.assertEqual(len(result["timeline"]), 25)
        self.assertEqual(result["timeline"][0]["cash_chf"], 20000)
        self.assertEqual(result["timeline"][12]["cash_chf"], 13500)
        self.assertEqual(result["timeline"][24]["cash_chf"], 37500)
        self.assertEqual(result["scenario"]["phases"][1]["monthly_income_chf"], 4000)
        self.assertEqual(extracted["scenario"]["opening_cash_chf"], 10000)

    def test_rejects_invalid_slider_values(self):
        with patch("financial_twin.server.urlopen", return_value=self.response(self.gemini_result())):
            extracted = extract("Study", 5, "test-key")
        data = {"scenario": extracted["scenario"], "years": 5, "variables": {
            "opening_cash_chf": 10000, "monthly_income_chf": -1, "monthly_expenses_chf": 2000,
        }}
        with self.assertRaises(ValueError):
            run_simulation(data)


    def test_node_narrative_uses_local_cash_flow(self):
        node = {"month": 1, "state": {"cash_chf": 1250, "net_worth_chf": 1250,
                "cash_flow": {"income_chf": 3000, "expenses_chf": 2000, "net_cash_flow_chf": 250}}}
        with patch("financial_twin.server.urlopen") as urlopen:
            result = node_narrative({"node": node})
        urlopen.assert_not_called()
        self.assertIn("income is CHF 3,000", result["narrative"])
        self.assertIn("cash changes by CHF 250 to CHF 1,250", result["narrative"])

    def test_branches_preserve_parent_and_siblings_across_nested_shocks(self):
        scenario = {
            "start_date": "2025-01-01", "opening_cash_chf": 10000,
            "phases": [{"name": "Work", "start_month": 0, "end_month": None,
                        "monthly_income_chf": 4000, "monthly_expenses_chf": 2000}],
            "assets": [{"id": "stocks", "type": "stocks", "value_chf": 10000,
                        "annual_return": 0, "annual_volatility": 0}],
            "debts": [],
        }
        parent = simulate(create_root(scenario), 12)[-1]
        original = deepcopy(parent)
        change = {"title": "Market crash", "summary": "The market falls.", "assumptions": "",
                  "changes": [], "one_offs": [], "assets": [], "investment_multiplier": 0.7}
        with patch("financial_twin.server.gemini_json", return_value=change):
            first = run_branch({"parent": parent, "event": "Market crash", "months": 2}, "key")["nodes"]
            sibling = run_branch({"parent": parent, "event": "Market crash", "months": 2}, "key")["nodes"]
            first_saved = deepcopy(first)
            nested = run_branch({"parent": first[0], "event": "Another crash", "months": 1}, "key")["nodes"]
        self.assertEqual(parent, original)
        self.assertEqual(first, first_saved)
        self.assertEqual(first[0]["parent_id"], parent["id"])
        self.assertEqual(sibling[0]["parent_id"], parent["id"])
        self.assertEqual(nested[0]["parent_id"], first[0]["id"])
        self.assertNotEqual(first[0]["id"], sibling[0]["id"])
        self.assertAlmostEqual(first[0]["state"]["assets"][0]["value_chf"], 7000)
        self.assertAlmostEqual(nested[0]["state"]["assets"][0]["value_chf"], 4900)
        self.assertAlmostEqual(first[0]["state"]["risk_exposure"]["investment_value_chf"], 7000)
        self.assertEqual(first[0]["event_narrative"], "The market falls.")
        self.assertNotIn("narrative", first[0])

    def test_branch_excludes_events_after_fork(self):
        scenario = {
            "start_date": "2025-01-01", "opening_cash_chf": 10000,
            "phases": [
                {"name": "B", "start_month": 0, "end_month": 3,
                 "monthly_income_chf": 4000, "monthly_expenses_chf": 2000},
                {"name": "C", "start_month": 3, "end_month": None,
                 "monthly_income_chf": 6000, "monthly_expenses_chf": 2000},
            ],
            "one_off_costs": [{"description": "C cost", "month": 3, "amount_chf": 1000}],
            "assets": [], "debts": [],
        }
        original = simulate(create_root(scenario), 5)
        change = {"title": "D", "summary": "", "assumptions": "", "one_offs": [], "assets": [],
                  "investment_multiplier": 1,
                  "changes": [{"kind": "income", "label": "Raise", "amount_chf": 500, "start_offset_months": 0,
                               "duration_months": 0, "source": "user"}]}
        with patch("financial_twin.server.gemini_json", return_value=change):
            branch = run_branch({"parent": original[1], "event": "D", "months": 3}, "key")["nodes"]
            boundary_branch = run_branch({"parent": original[2], "event": "D", "months": 2}, "key")["nodes"]
        self.assertEqual(branch[0]["event_summary"], "D")
        self.assertEqual(original[3]["event_summary"], "C; C cost")
        self.assertEqual(branch[1]["event_summary"], "Monthly update")
        self.assertEqual(branch[1]["state"]["cash_flow"]["income_chf"], 4500)
        self.assertEqual(branch[1]["state"]["cash_flow"]["one_off_costs_chf"], 0)
        self.assertEqual(boundary_branch[0]["state"]["cash_flow"]["income_chf"], 4500)
        self.assertEqual(boundary_branch[0]["state"]["cash_flow"]["one_off_costs_chf"], 0)


if __name__ == "__main__":
    unittest.main()
