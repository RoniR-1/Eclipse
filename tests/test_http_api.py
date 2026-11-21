"""HTTP API: routing, request validation, error responses and static files.

Starts the real request handler on a free local port. The AI provider is stubbed and the database is not
needed, because the handler only touches it at server start-up.
"""

import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from financial_twin import server

SCENARIO = {
    "start_date": "2026-01-01", "currency": "CHF", "opening_cash_chf": 8000, "annual_inflation": 0,
    "phases": [{"name": "Now", "start_month": 0, "end_month": None, "monthly_income_chf": 4000, "monthly_expenses_chf": 3000}],
    "one_off_costs": [], "assets": [], "debts": [],
    "preferences": {"savings_rate": 0.5, "risk_tolerance": 0.5, "safety_priority": 0.5, "emergency_months": 3,
                    "investment_annual_return": 0.04, "investment_annual_volatility": 0.1},
}
VARIABLES = {"opening_cash_chf": 8000, "monthly_income_chf": 4000, "savings_rate": 0.5}
CHANGE = {"monthly_income_delta_chf": 0, "monthly_expenses_delta_chf": 500, "one_off_cost_chf": 2000,
          "investment_multiplier": 1, "new_asset_chf": 0, "new_debt_chf": 0,
          "new_debt_annual_interest_rate": 0, "new_debt_monthly_payment_chf": 0}


class HttpApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.httpd.RequestHandlerClass.log_message = lambda *args: None  # keep test output quiet
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def call(self, path, body=None, *, raw=None, content_type="application/json", method=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        request = Request(self.base + path, data=data, method=method or ("POST" if data is not None else "GET"))
        if data is not None:
            request.add_header("Content-Type", content_type)
        try:
            with urlopen(request) as response:
                return response.status, response.headers, response.read()
        except HTTPError as error:
            with error:
                return error.code, error.headers, error.read()

    def call_json(self, path, body=None, **kwargs):
        status, headers, payload = self.call(path, body, **kwargs)
        return status, json.loads(payload)

    def test_serves_the_website(self):
        status, headers, body = self.call("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn(b"app.js", body)
        self.assertEqual(self.call("/app.js")[0], 200)
        self.assertEqual(self.call("/styles.css")[0], 200)

    def test_does_not_serve_files_outside_the_web_folder(self):
        status, _, body = self.call("/../financial_twin/server.py")
        self.assertNotIn(b"def run_simulation", body)
        self.assertNotIn(b"AI_API_KEY", self.call("/../.env")[2])

    def test_config_reports_whether_a_key_is_configured_without_revealing_it(self):
        with patch.dict(os.environ, {"AI_API_KEY": "secret-key"}, clear=False):
            status, data = self.call_json("/api/config")
        self.assertEqual((status, data), (200, {"has_api_key": True}))
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.call_json("/api/config")[1], {"has_api_key": False})

    def test_schema(self):
        status, data = self.call_json("/api/schema")
        self.assertEqual(status, 200)
        self.assertIsInstance(data, dict)
        self.assertTrue(data)

    def test_simulate(self):
        status, data = self.call_json("/api/simulate", {"scenario": SCENARIO, "years": 1, "variables": VARIABLES})
        self.assertEqual(status, 200)
        self.assertEqual(len(data["nodes"]), 13)
        self.assertEqual(data["nodes"][-1]["month"], 12)
        self.assertIn("numpy", data["environment"])

    def test_branch_uses_the_ai_change_and_returns_a_replayable_record(self):
        nodes = self.call_json("/api/simulate", {"scenario": SCENARIO, "years": 1, "variables": VARIABLES})[1]["nodes"]
        with patch("financial_twin.server.gemini_json", return_value=CHANGE) as ai:
            status, data = self.call_json("/api/branch", {"parent": nodes[3], "event": "Move flat", "months": 6, "sequence": 0, "api_key": "k"})
        self.assertEqual(status, 200)
        ai.assert_called_once()
        self.assertEqual(len(data["nodes"]), 6)
        self.assertEqual(data["branch"]["change"], CHANGE)
        self.assertEqual(data["branch"]["parent_id"], nodes[3]["id"])
        self.assertEqual(data["nodes"][0]["state"]["cash_flow"]["one_off_costs_chf"], 2000)

    def test_narrative_and_test_data(self):
        node = self.call_json("/api/simulate", {"scenario": SCENARIO, "years": 1, "variables": VARIABLES})[1]["nodes"][1]
        status, data = self.call_json("/api/narrative", {"node": node})
        self.assertEqual(status, 200)
        self.assertIn("CHF", data["narrative"])
        status, data = self.call_json("/api/test", {"years": 5, "seed": 3, "start_date": "2026-01-01"})
        self.assertEqual((status, data["seed"]), (200, 3))

    def test_projection(self):
        status, data = self.call_json("/api/project", {
            "profile": {"age": 20, "cash_chf": 8000, "monthly_net_income_chf": 1200, "monthly_rent_chf": 900, "employment_status": "student"},
            "scenarios": {"Master's": [{"type": "education", "date": "2027-09"}]}, "horizon_years": 2, "start": "2026-11"})
        self.assertEqual(status, 200)
        self.assertIn("comparisons", data)

    def test_extract_without_a_key_fails_cleanly_without_calling_the_ai(self):
        with patch.dict(os.environ, {}, clear=True), patch("financial_twin.server.urlopen") as network:
            status, data = self.call_json("/api/extract", {"statement": "I earn CHF 5000 a month.", "years": 5})
        self.assertEqual(status, 400)
        self.assertIn("API key", data["error"])
        network.assert_not_called()

    def test_malformed_requests_get_a_json_400(self):
        cases = {
            "wrong content type": dict(raw=b"{}", content_type="text/plain"),
            "empty body": dict(raw=b""),
            "invalid JSON": dict(raw=b"{not json"),
            "not an object": dict(raw=b"[1, 2]"),
            "too large": dict(raw=b"{" + b" " * 100001 + b"}"),
            "invalid simulation input": dict(body={"scenario": SCENARIO, "years": 21, "variables": VARIABLES}),
            "unknown variable": dict(body={"scenario": SCENARIO, "years": 1, "variables": {"bogus": 1, "savings_rate": 0.1, "risk_tolerance": 0.2}}),
        }
        for name, kwargs in cases.items():
            with self.subTest(name):
                body = kwargs.pop("body", None)
                status, headers, payload = self.call("/api/simulate", body, **kwargs)
                self.assertEqual(status, 400)
                self.assertIn("application/json", headers["Content-Type"])
                self.assertTrue(json.loads(payload)["error"])

    def test_unknown_endpoint_is_404(self):
        status, data = self.call_json("/api/nothing", {})
        self.assertEqual(status, 404)
        self.assertEqual(data, {"error": "Not found."})

    def test_unexpected_api_failures_return_generic_500_and_server_recovers(self):
        cases = (
            ("financial_twin.projection.schema", "/api/schema", None),
            ("financial_twin.server.run_simulation", "/api/simulate", {"scenario": SCENARIO}),
        )
        for target, path, body in cases:
            with self.subTest(path=path), self.assertLogs("financial_twin.server", level="ERROR") as logs:
                with patch(target, side_effect=RuntimeError("private request content")):
                    status, data = self.call_json(path, body)
            self.assertEqual(status, 500)
            self.assertEqual(data, {"error": "An unexpected server error occurred. Please try again."})
            self.assertIn("RuntimeError", logs.output[0])
            self.assertNotIn("private request content", logs.output[0])
            self.assertEqual(self.call_json("/api/config")[0], 200)

    def test_responses_are_not_cached(self):
        _, headers, _ = self.call("/api/config")
        self.assertEqual(headers["Cache-Control"], "no-store")


if __name__ == "__main__":
    unittest.main()
