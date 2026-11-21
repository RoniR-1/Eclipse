"""Same input, same output: the AI's answers are cached, so a repeated question gets the same numbers.

The fake AI used here deliberately answers differently on every call, the way a real language model can.
"""

from copy import deepcopy
import io
import itertools
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.request import Request, urlopen

from financial_twin import server
from financial_twin.server import extract, run_branch, run_simulation

STORY = "I have CHF 15,000 saved, earn CHF 5,000 a month and spend CHF 3,500. I might start a master's next year."


def scenario_answer(cash):
    return {"summary": "Saving towards a master's", "assumptions": "Rent included in spending",
            "opening_cash_chf": cash, "annual_inflation": 0.01 + cash / 1e7,
            "phases": [{"name": "Work", "start_month": 0, "monthly_income_chf": 5000, "monthly_expenses_chf": 3500 + cash / 100}],
            "one_off_costs": [], "assets": [], "debts": [],
            "preferences": {"savings_rate": 0.4, "risk_tolerance": 0.5, "safety_priority": 0.5, "emergency_months": 3,
                            "investment_annual_return": 0.04, "investment_annual_volatility": 0.12},
            "variable_ids": ["opening_cash_chf", "monthly_income_chf", "savings_rate"]}


def branch_answer(cost):
    return {"monthly_income_delta_chf": -3000, "monthly_expenses_delta_chf": 200, "one_off_cost_chf": cost,
            "investment_multiplier": 1, "new_asset_chf": 0, "new_debt_chf": 0,
            "new_debt_annual_interest_rate": 0, "new_debt_monthly_payment_chf": 0}


class InconsistentAI:
    """A fake AI that gives a different answer every time it is asked."""

    def __init__(self):
        self.calls = 0
        self.counter = itertools.count(1)

    def __call__(self, prompt, schema, api_key=None):
        self.calls += 1
        step = next(self.counter)
        return branch_answer(1000 * step) if schema is server.BRANCH_SCHEMA else scenario_answer(10000 + 1000 * step)


def final_numbers(extraction, years=5):
    variables = {item["id"]: item["value"] for item in extraction["variables"]}
    nodes = run_simulation({"scenario": extraction["scenario"], "years": years, "variables": variables})["nodes"]
    return [(node["month"], node["state"]["cash_chf"], node["state"]["net_worth_chf"]) for node in nodes]


class CacheTestCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.cache = Path(folder.name)
        self.ai = InconsistentAI()
        for item in (patch.object(server, "AI_CACHE_DIR", self.cache), patch("financial_twin.server.gemini_json", side_effect=self.ai)):
            item.start()
            self.addCleanup(item.stop)


class SameStorySameNumbersTests(CacheTestCase):
    def test_the_fake_ai_really_is_inconsistent_without_the_cache(self):
        with patch.object(server, "AI_CACHE_DIR", None):
            first, second = extract(STORY, 5, "key"), extract(STORY, 5, "key")
        self.assertNotEqual(final_numbers(first), final_numbers(second))
        self.assertEqual(self.ai.calls, 2)

    def test_same_story_gives_the_same_assumptions_and_the_same_numbers(self):
        first = extract(STORY, 5, "key")
        second = extract(STORY, 5, "key")
        self.assertEqual(self.ai.calls, 1, "the second request is answered from the cache")
        self.assertEqual(first, second)
        self.assertEqual(final_numbers(first), final_numbers(second))

    def test_whitespace_differences_count_as_the_same_story(self):
        extract(STORY, 5, "key")
        extract("  " + STORY.replace(" ", "  ") + "\n", 5, "key")
        self.assertEqual(self.ai.calls, 1)

    def test_a_different_story_or_horizon_gets_a_fresh_answer(self):
        base = extract(STORY, 5, "key")
        changed = extract(STORY + " I also have a car.", 5, "key")
        longer = extract(STORY, 10, "key")
        self.assertEqual(self.ai.calls, 3)
        self.assertNotEqual(base["scenario"], changed["scenario"])
        self.assertNotEqual(base["scenario"], longer["scenario"])

    def test_answers_survive_a_restart(self):
        first = extract(STORY, 5, "key")
        self.assertEqual(len(list(self.cache.glob("extract-*.json"))), 1)
        with patch("financial_twin.server.gemini_json", side_effect=AssertionError("the AI must not be asked again")):
            self.assertEqual(extract(STORY, 5, "key"), first)

    def test_invalid_answers_are_not_cached(self):
        broken = scenario_answer(15000)
        broken["phases"][0]["start_month"] = 3  # no phase starting at month 0
        with patch("financial_twin.server.gemini_json", return_value=broken), self.assertRaises(ValueError):
            extract(STORY, 5, "key")
        self.assertEqual(list(self.cache.iterdir()), [])
        good = extract(STORY, 5, "key")
        self.assertEqual(extract(STORY, 5, "key"), good)
        self.assertEqual(self.ai.calls, 1)

    def test_a_damaged_cache_entry_is_replaced(self):
        extract(STORY, 5, "key")
        entry = next(self.cache.glob("extract-*.json"))
        entry.write_text("{not json", encoding="utf-8")
        repaired = extract(STORY, 5, "key")
        self.assertEqual(self.ai.calls, 2)
        self.assertEqual(extract(STORY, 5, "key"), repaired)
        self.assertEqual(self.ai.calls, 2)

    def test_cache_write_failure_returns_the_validated_answer_and_cleans_up(self):
        with patch("financial_twin.server.os.replace", side_effect=OSError("disk unavailable")):
            result = extract(STORY, 5, "key")
        self.assertEqual(result["scenario"]["opening_cash_chf"], 11000)
        self.assertEqual(list(self.cache.iterdir()), [])
        self.assertEqual(self.ai.calls, 1)

    def test_cache_directory_failure_returns_the_validated_answer(self):
        with patch.object(Path, "mkdir", side_effect=OSError("disk unavailable")):
            result = extract(STORY, 5, "key")
        self.assertEqual(result["scenario"]["opening_cash_chf"], 11000)
        self.assertEqual(self.ai.calls, 1)

    def test_the_first_stored_answer_wins_when_two_requests_race(self):
        prompt, schema = "question", {"type": "object"}
        self.assertEqual(server.store_ai_answer("extract", prompt, schema, {"answer": "A"}), {"answer": "A"})
        self.assertEqual(server.store_ai_answer("extract", prompt, schema, {"answer": "B"}), {"answer": "A"})
        self.assertEqual(server.cached_ai_answer("extract", prompt, schema), {"answer": "A"})

    def test_concurrent_identical_requests_agree(self):
        results = []
        threads = [threading.Thread(target=lambda: results.append(extract(STORY, 5, "key"))) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(results), 8)
        self.assertTrue(all(result == results[0] for result in results))

    def test_api_keys_and_models_do_not_affect_the_answer(self):
        first = extract(STORY, 5, "key-one")
        with patch.dict(os.environ, {"AI_API_KEY_MODEL": "another-model"}):
            second = extract(STORY, 5, "key-two")
        self.assertEqual(first, second)
        self.assertEqual(self.ai.calls, 1)


class SameEventSameBranchTests(CacheTestCase):
    def setUp(self):
        super().setUp()
        extraction = extract(STORY, 5, "key")
        variables = {item["id"]: item["value"] for item in extraction["variables"]}
        self.nodes = run_simulation({"scenario": extraction["scenario"], "years": 5, "variables": variables})["nodes"]

    def branch_numbers(self, event, parent=None, sequence=0):
        nodes = run_branch({"parent": parent or self.nodes[12], "event": event, "months": 24, "sequence": sequence}, "key")["nodes"]
        return [(node["month"], node["state"]["cash_chf"], node["state"]["net_worth_chf"]) for node in nodes]

    def test_same_event_on_the_same_moment_gives_the_same_branch(self):
        calls_before = self.ai.calls
        first = self.branch_numbers("Start a master's at EPFL")
        second = self.branch_numbers("Start a master's at EPFL", sequence=1)
        self.assertEqual(first, second)
        self.assertEqual(self.ai.calls, calls_before + 1)

    def test_a_different_event_or_moment_gets_a_fresh_answer(self):
        calls_before = self.ai.calls
        base = self.branch_numbers("Start a master's at EPFL")
        other_event = self.branch_numbers("Start a master's at ETH")
        other_moment = self.branch_numbers("Start a master's at EPFL", parent=self.nodes[24])
        self.assertEqual(self.ai.calls, calls_before + 3)
        self.assertNotEqual(base, other_event)
        self.assertNotEqual(base[0], other_moment[0])


class EndToEndTests(CacheTestCase):
    def test_same_story_through_the_website_api_gives_identical_numbers(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        httpd.RequestHandlerClass.log_message = lambda *args: None
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)

        def post(path, body):
            request = Request(f"http://127.0.0.1:{httpd.server_port}{path}", json.dumps(body).encode(),
                              {"Content-Type": "application/json"})
            with urlopen(request) as response:
                return json.loads(response.read())

        runs = []
        for _ in range(2):
            extraction = post("/api/extract", {"statement": STORY, "years": 5, "api_key": "key"})
            variables = {item["id"]: item["value"] for item in extraction["variables"]}
            simulated = post("/api/simulate", {"scenario": extraction["scenario"], "years": 5, "variables": variables})
            runs.append([(n["month"], n["state"]["cash_chf"], n["state"]["net_worth_chf"]) for n in simulated["nodes"]])
        self.assertEqual(runs[0], runs[1])
        self.assertEqual(self.ai.calls, 1)


class RequestSettingsTests(unittest.TestCase):
    def capture(self, environment):
        sent = []

        def provider(request, timeout):
            sent.append(json.loads(request.data))
            if "generationConfig" in sent[-1]:
                return io.BytesIO(json.dumps({"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}).encode())
            return io.BytesIO(json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode())

        with patch.dict(os.environ, environment, clear=True), patch("financial_twin.server.urlopen", side_effect=provider):
            server.gemini_json("prompt", {}, "key")
        return sent[0]

    def test_requests_ask_for_the_most_repeatable_answers(self):
        custom = self.capture({"AI_BASE_URL": "https://example.test/v1", "AI_API_KEY_MODEL": "m"})
        self.assertEqual(custom["temperature"], 0)
        gemini = self.capture({})
        self.assertEqual(gemini["generationConfig"]["temperature"], 0)
        self.assertEqual(gemini["generationConfig"]["seed"], 0)

    def test_temperature_can_be_left_to_the_provider_or_changed(self):
        self.assertNotIn("temperature", self.capture({"AI_BASE_URL": "https://example.test/v1", "AI_API_KEY_MODEL": "m", "AI_TEMPERATURE": ""}))
        self.assertEqual(self.capture({"AI_BASE_URL": "https://example.test/v1", "AI_API_KEY_MODEL": "m", "AI_TEMPERATURE": "0.3"})["temperature"], 0.3)
        for bad in ("warm", "-1", "nan"):
            with self.subTest(bad), self.assertRaises(ValueError):
                self.capture({"AI_BASE_URL": "https://example.test/v1", "AI_API_KEY_MODEL": "m", "AI_TEMPERATURE": bad})

    def test_the_cache_is_off_unless_the_server_turns_it_on(self):
        self.assertIsNone(server.AI_CACHE_DIR)

    def test_server_start_turns_the_cache_on(self):
        try:
            import financial_twin.database  # noqa: F401  (needs psycopg)
        except ImportError:
            self.skipTest("psycopg is not installed")

        class FakeServer:
            def __init__(self, *args):
                pass

            def serve_forever(self):
                pass

        with patch.object(server, "AI_CACHE_DIR", None), patch.object(server, "load_env"), \
                patch("financial_twin.database.initialize_from_environment"), \
                patch.object(server, "ThreadingHTTPServer", FakeServer), patch("builtins.print"), \
                patch.dict(os.environ, {}, clear=True):
            server.main()
            self.assertEqual(server.AI_CACHE_DIR, server.ROOT / ".local" / "ai-cache")
        with patch.object(server, "AI_CACHE_DIR", None), patch.object(server, "load_env"), \
                patch("financial_twin.database.initialize_from_environment"), \
                patch.object(server, "ThreadingHTTPServer", FakeServer), patch("builtins.print"), \
                patch.dict(os.environ, {"AI_CACHE_DIR": "/tmp/somewhere"}, clear=True):
            server.main()
            self.assertEqual(server.AI_CACHE_DIR, Path("/tmp/somewhere"))


if __name__ == "__main__":
    unittest.main()
