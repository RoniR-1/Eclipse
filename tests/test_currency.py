"""Currencies: amounts in other currencies are converted to CHF exactly, with documented rates.

The engine always calculates in CHF; the chosen currency only changes what is shown and how typed or written
amounts are read. No test here uses the network: the rate service is replaced by a fake.
"""

import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from financial_twin import currency, server
from tests.test_ai_cache import branch_answer, scenario_answer

LIVE = {"date": "2026-11-20", "rates": {**currency.BUILT_IN["rates"], "EUR": 1.25}}  # 1 EUR = 0.8 CHF
RATES = lambda: {**LIVE["rates"]}


class FakeRates:
    def __init__(self, answer=LIVE):
        self.answer = answer
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if isinstance(self.answer, Exception):
            raise self.answer
        return json.loads(json.dumps(self.answer))


class CurrencyTestCase(unittest.TestCase):
    def setUp(self):
        currency.reset()
        self.addCleanup(currency.reset)
        self.fetch = FakeRates()
        for patcher in (patch.object(currency, "fetch_rates", self.fetch), patch.object(currency, "RATES_FILE", None)):
            patcher.start()
            self.addCleanup(patcher.stop)


class StoryAmountTests(CurrencyTestCase):
    def convert(self, text, chosen="EUR"):
        return currency.convert_text(text, chosen, RATES)

    def test_amounts_written_in_another_currency_become_chf(self):
        text, conversions = self.convert("I have €15,000 saved, earn 4'200 EUR a month and pay 1.200 € rent.")
        self.assertEqual(text, "I have CHF 12,000.00 (written as €15,000) saved, earn CHF 3,360.00 (written as 4'200 EUR) "
                               "a month and pay CHF 960.00 (written as 1.200 €) rent.")
        self.assertEqual([item["amount"] for item in conversions], [15000, 4200, 1200])

    def test_scales_words_and_other_currencies(self):
        text, conversions = self.convert("a 50k euros loan, 2 million yen, £1.5m and USD 20,000")
        self.assertEqual([item["currency"] for item in conversions], ["EUR", "JPY", "GBP", "USD"])
        self.assertEqual([item["amount"] for item in conversions], [50_000, 2_000_000, 1_500_000, 20_000])
        self.assertEqual(conversions[0]["chf"], 40_000)
        self.assertEqual(conversions[1]["chf"], round(2_000_000 / LIVE["rates"]["JPY"], 2))

    def test_chf_amounts_bare_numbers_and_ordinary_words_are_left_alone(self):
        story = "I have CHF 2,000 and Fr. 300, 15000 saved, 3 years, 2 kids, a neuro 500 course and 40 francs."
        self.assertEqual(self.convert(story), (story, []))

    def test_shared_symbols_follow_the_chosen_currency(self):
        self.assertEqual(self.convert("$1,000", "CAD")[1][0]["currency"], "CAD")
        self.assertEqual(self.convert("$1,000", "EUR")[1][0]["currency"], "USD")
        self.assertEqual(self.convert("¥1000", "CNY")[1][0]["currency"], "CNY")
        self.assertEqual(self.convert("kr 500", "SEK")[1][0]["currency"], "SEK")
        self.assertEqual(self.convert("kr 500", "EUR")[1], [], "kr is ambiguous without a Nordic currency chosen")


class RateTests(CurrencyTestCase):
    def test_live_rates_are_fetched_once_and_reused(self):
        first = currency.current_rates()
        currency.current_rates()
        self.assertEqual(self.fetch.calls, 1)
        self.assertTrue(first["live"])
        self.assertEqual(first["source"], "European Central Bank reference rate")
        self.assertEqual(currency.context("eur")["per_chf"], 1.25)

    def test_chf_needs_no_rates(self):
        self.assertEqual(currency.context(None), {"code": "CHF", "per_chf": 1.0, "date": None, "source": None})
        self.assertEqual(currency.prepare_for_ai("I have CHF 2,000.", "CHF")[3], [])
        self.assertEqual(self.fetch.calls, 0)

    def test_unsupported_currencies_are_rejected(self):
        for code in ("XYZ", "", 5, "BTC"):
            with self.assertRaises(ValueError):
                currency.context(code)

    def test_offline_uses_saved_rates_then_built_in_rates(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(currency, "RATES_FILE", Path(folder) / "rates.json"):
                currency.current_rates()  # live: saved to the file
                currency.reset()
                self.fetch.answer = URLError("offline")
                saved = currency.current_rates()
                self.assertFalse(saved["live"])
                self.assertIn("saved, offline", saved["source"])
                self.assertEqual(saved["rates"]["EUR"], 1.25)
            currency.reset()
            built_in = currency.current_rates()
            self.assertIn("built in, offline", built_in["source"])
            self.assertEqual(built_in["date"], currency.BUILT_IN["date"])
            currency.current_rates()
            self.assertEqual(self.fetch.calls, 3, "a failed fetch is not retried on every request")

    def test_offline_rates_are_retried_sooner_than_live_ones(self):
        clock = [1000.0]
        with patch.object(currency, "monotonic", lambda: clock[0]):
            self.fetch.answer = URLError("offline")
            self.assertFalse(currency.current_rates()["live"])
            clock[0] += currency.RETRY_SECONDS
            self.fetch.answer = LIVE
            self.assertTrue(currency.current_rates()["live"], "back online after the retry pause")
            clock[0] += currency.RETRY_SECONDS
            currency.current_rates()
            self.assertEqual(self.fetch.calls, 2, "live rates are kept for hours")
            clock[0] += currency.REFRESH_SECONDS
            currency.current_rates()
            self.assertEqual(self.fetch.calls, 3)

    def test_a_malformed_rate_answer_is_not_used(self):
        self.fetch.answer = ValueError("unexpected exchange-rate answer")
        self.assertEqual(currency.current_rates()["rates"], currency.BUILT_IN["rates"])

    def test_every_currency_has_a_built_in_rate(self):
        self.assertEqual(set(currency.BUILT_IN["rates"]) | {"CHF"}, set(currency.CURRENCIES))
        listed = currency.currency_list()
        self.assertEqual([item["code"] for item in listed["currencies"]][0], "CHF")
        self.assertEqual(listed["currencies"][0]["per_chf"], 1.0)


class PromptTests(CurrencyTestCase):
    def test_the_story_reaches_the_ai_in_chf_with_the_rate_for_bare_amounts(self):
        prompts = []

        def ai(prompt, schema, api_key=None, **options):
            prompts.append(prompt)
            return scenario_answer(12000)

        with patch.object(server, "gemini_json", ai):
            result = server.extract("I have €15,000 saved and earn 4,000 a month.", 5, "key", "EUR")
        self.assertIn("CHF 12,000.00 (written as €15,000)", prompts[0])
        self.assertIn("Any amount written without a currency is in EUR", prompts[0])
        self.assertIn("1 EUR = 0.800000 CHF", prompts[0])
        self.assertEqual(result["currency"]["code"], "EUR")
        self.assertEqual(result["currency"]["per_chf"], 1.25)
        self.assertEqual(result["conversions"][0]["chf"], 12000)
        self.assertEqual(result["scenario"]["currency"], "CHF", "the engine still calculates in CHF")

    def test_chf_prompts_are_unchanged(self):
        prompts = []
        with patch.object(server, "gemini_json", lambda prompt, *a, **k: prompts.append(prompt) or scenario_answer(9000)):
            server.extract("I have CHF 9,000 saved.", 5, "key")
        self.assertNotIn("chose", prompts[0])
        self.assertEqual(self.fetch.calls, 0)

    def test_branch_events_are_converted_but_keep_their_wording_as_the_name(self):
        nodes = server.run_simulation({"scenario": server.random_test({"years": 1, "seed": 3})["extraction"]["scenario"],
                                       "years": 1, "variables": {"opening_cash_chf": 9000, "monthly_income_chf": 5000,
                                                                 "savings_rate": 0.3}})["nodes"]
        prompts = []
        with patch.object(server, "gemini_json", lambda prompt, *a, **k: prompts.append(prompt) or branch_answer(4000)):
            result = server.run_branch({"parent": nodes[3], "event": "Buy a car for €5,000", "months": 6,
                                        "currency": "EUR", "sequence": 0}, "key")
        self.assertIn('"event": "Buy a car for CHF 4,000.00 (written as \\u20ac5,000)"', prompts[0])
        self.assertEqual(result["conversions"][0]["amount"], 5000)
        self.assertIn("€5,000", result["nodes"][0]["event_summary"])


    def test_branch_proposals_get_the_event_in_chf(self):
        from tests.test_building_blocks import parent_node, proposal
        prompts = []
        plan = proposal(title="Car", one_offs=[{"label": "Car", "amount_chf": 4000, "offset_months": 0, "source": "user"}])
        with patch.object(server, "gemini_json", lambda prompt, *a, **k: prompts.append(prompt) or plan):
            result = server.propose_branch({"parent": parent_node(), "event": "Buy a car for €5,000", "currency": "EUR"}, "key")
        self.assertIn("CHF 4,000.00 (written as", prompts[0])
        self.assertIn("1 EUR = 0.800000 CHF", prompts[0])
        self.assertEqual(result["currency"]["code"], "EUR")
        self.assertEqual(result["conversions"][0]["chf"], 4000)
        with patch.object(server, "gemini_json", lambda prompt, *a, **k: prompts.append(prompt) or plan):
            server.propose_branch({"parent": parent_node(), "preset": "market_shock"}, "key")
        self.assertNotIn("chose", prompts[-1], "CHF and no event text: the prompt is unchanged")

class HttpTests(CurrencyTestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.httpd.RequestHandlerClass.log_message = lambda *args: None
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def call(self, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = Request(self.base + path, data=data, headers={"Content-Type": "application/json"} if data else {})
        try:
            with urlopen(request) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def test_currencies_endpoint(self):
        status, data = self.call("/api/currencies")
        self.assertEqual(status, 200)
        self.assertEqual(data["base"], "CHF")
        self.assertEqual(len(data["currencies"]), len(currency.CURRENCIES))
        self.assertEqual(next(item for item in data["currencies"] if item["code"] == "EUR")["per_chf"], 1.25)

    def test_an_unsupported_currency_is_a_400(self):
        with patch.dict(os.environ, {"AI_API_KEY": "key"}), patch.object(server, "gemini_json") as ai:
            status, data = self.call("/api/extract", {"statement": "I have 5,000 saved.", "years": 5, "currency": "XYZ"})
        self.assertEqual(status, 400)
        self.assertIn("Unsupported currency", data["error"])
        ai.assert_not_called()


if __name__ == "__main__":
    unittest.main()
