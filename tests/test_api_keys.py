"""Cycling through several API keys configured in .env (AI_API_KEY_1, AI_API_KEY_2, ...)."""

import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from financial_twin import server
from financial_twin.server import configured_api_keys, gemini_json

KEYS = {"AI_API_KEY_1": "key-one", "AI_API_KEY_2": "key-two", "AI_API_KEY_3": "key-three", "AI_API_KEY_4": "key-four"}
PROVIDER = {"AI_BASE_URL": "https://example.test/v1", "AI_API_KEY_MODEL": "model-a", "AI_MODEL_RETRY_SECONDS": "60"}


def ok():
    return io.BytesIO(json.dumps({"choices": [{"message": {"content": '{"ok": true}'}}]}).encode())


class FakeProvider:
    """Records which key each request used and fails for the keys told to."""

    def __init__(self, failing=None):
        self.failing = failing or {}  # key -> HTTP status or exception
        self.used = []

    def __call__(self, request, timeout):
        key = request.get_header("Authorization").removeprefix("Bearer ")
        self.used.append(key)
        failure = self.failing.get(key)
        if isinstance(failure, int):
            raise HTTPError(request.full_url, failure, "error", {}, io.BytesIO(b""))
        if failure is not None:
            raise failure
        return ok()


class ApiKeyRotationTests(unittest.TestCase):
    def setUp(self):
        patches = [patch.dict("os.environ", {**KEYS, **PROVIDER}, clear=True),
                   patch.dict(server.KEY_COOLDOWNS, {}, clear=True),
                   patch.dict(server.MODEL_COOLDOWNS, {}, clear=True),
                   patch.object(server, "KEY_ROTATION", 0)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def run_requests(self, provider, count, key=None):
        with patch("financial_twin.server.urlopen", side_effect=provider):
            return [gemini_json("prompt", {}, key) for _ in range(count)]

    def test_numbered_keys_are_read_in_numeric_order(self):
        with patch.dict("os.environ", {"AI_API_KEY_10": "ten", "AI_API_KEY_2": "two", "AI_API_KEY_1": " one ",
                                       "AI_API_KEY_3": "", "AI_API_KEY_X": "ignored", "AI_API_KEY_MODEL": "not-a-key"}, clear=True):
            self.assertEqual(configured_api_keys(), ["one", "two", "ten"])
        with patch.dict("os.environ", {"AI_API_KEY": "single", "AI_API_KEY_1": "single", "AI_API_KEY_2": "two"}, clear=True):
            self.assertEqual(configured_api_keys(), ["single", "two"], "the single key still works and duplicates are skipped")
        with patch.dict("os.environ", {"GEMINI_API_KEY": "gemini"}, clear=True):
            self.assertEqual(configured_api_keys(), ["gemini"])
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(configured_api_keys(), [])

    def test_requests_take_turns_across_all_keys(self):
        provider = FakeProvider()
        self.run_requests(provider, 8)
        self.assertEqual(provider.used, ["key-one", "key-two", "key-three", "key-four"] * 2)

    def test_rate_limited_key_is_skipped_within_the_same_request_and_then_rested(self):
        provider = FakeProvider({"key-one": 429})
        results = self.run_requests(provider, 5)
        self.assertEqual(results, [{"ok": True}] * 5)
        # Request 1 fails over from key-one to key-two; key-one then rests and is tried last.
        self.assertEqual(provider.used, ["key-one", "key-two", "key-two", "key-three", "key-four", "key-two"])

    def test_rested_key_returns_after_the_retry_period(self):
        provider = FakeProvider({"key-one": 429})
        with patch("financial_twin.server.urlopen", side_effect=provider), patch("financial_twin.server.monotonic") as now:
            now.return_value = 100
            gemini_json("prompt", {})            # key-one fails, key-two answers
            provider.failing.clear()
            now.return_value = 150
            for _ in range(3):
                gemini_json("prompt", {})        # key-one still resting
            now.return_value = 161
            for _ in range(4):
                gemini_json("prompt", {})
        self.assertEqual(provider.used[:5], ["key-one", "key-two", "key-two", "key-three", "key-four"])
        self.assertIn("key-one", provider.used[5:], "key-one is used again after 60 seconds")

    def test_rejected_key_is_skipped(self):
        for status in (401, 403):
            with self.subTest(status=status), patch.dict(server.KEY_COOLDOWNS, {}, clear=True), patch.object(server, "KEY_ROTATION", 0):
                provider = FakeProvider({"key-one": status})
                self.assertEqual(self.run_requests(provider, 1), [{"ok": True}])
                self.assertEqual(provider.used, ["key-one", "key-two"])

    def test_all_keys_rate_limited_reports_the_rate_limit(self):
        provider = FakeProvider({key: 429 for key in KEYS.values()})
        with self.assertRaisesRegex(ValueError, "rate limit"):
            self.run_requests(provider, 1)
        self.assertEqual(provider.used, ["key-one", "key-two", "key-three", "key-four"])

    def test_problems_unrelated_to_the_key_do_not_burn_through_the_other_keys(self):
        for failure in (URLError("offline"), 400):
            with self.subTest(failure=failure), patch.dict(server.KEY_COOLDOWNS, {}, clear=True), patch.object(server, "KEY_ROTATION", 0):
                provider = FakeProvider({key: failure for key in KEYS.values()})
                with self.assertRaises(ValueError):
                    self.run_requests(provider, 1)
                self.assertEqual(provider.used, ["key-one"])

    def test_key_entered_on_the_website_is_used_on_its_own(self):
        provider = FakeProvider()
        self.run_requests(provider, 3, key="website-key")
        self.assertEqual(provider.used, ["website-key"] * 3)

    def test_keys_also_rotate_without_a_custom_endpoint(self):
        used = []

        def gemini(request, timeout):
            used.append(request.get_header("X-goog-api-key"))
            return io.BytesIO(json.dumps({"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}).encode())

        with patch.dict("os.environ", {"AI_BASE_URL": ""}), patch("financial_twin.server.urlopen", side_effect=gemini):
            for _ in range(5):
                gemini_json("prompt", {})
        self.assertEqual(used, ["key-one", "key-two", "key-three", "key-four", "key-one"])

    def test_no_keys_configured_explains_what_to_set(self):
        with patch.dict("os.environ", {}, clear=True), self.assertRaisesRegex(ValueError, "AI_API_KEY_1"):
            gemini_json("prompt", {})

    def test_keys_are_never_sent_to_the_browser(self):
        from financial_twin.server import Handler
        sent = {}
        handler = Handler.__new__(Handler)
        handler.path = "/api/config"
        handler.send_json = lambda status, data: sent.update(status=status, data=data)
        handler.do_GET()
        self.assertEqual(sent, {"status": 200, "data": {"has_api_key": True}})
        self.assertNotIn("key-one", json.dumps(sent))



class CheckKeysScriptTests(unittest.TestCase):
    """scripts/check_keys.py, run against a fake provider."""

    def run_script(self, environment, failing=None):
        import contextlib
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        import check_keys
        output = io.StringIO()
        with patch.dict("os.environ", environment, clear=True), patch.object(server, "load_env", lambda: None), \
                patch("financial_twin.server.urlopen", side_effect=FakeProvider(failing)), \
                patch.dict(server.KEY_COOLDOWNS, {}, clear=True), patch.object(server, "KEY_ROTATION", 0), \
                contextlib.redirect_stdout(output):
            status = check_keys.main(["--rotation", "4"])
        return status, output.getvalue()

    def test_all_keys_working(self):
        status, output = self.run_script({**KEYS, **PROVIDER})
        self.assertEqual(status, 0)
        self.assertIn("4 loaded", output)
        self.assertIn("All keys work.", output)
        for key in KEYS.values():
            self.assertNotIn(key, output, "full keys are never printed")

    def test_failing_key_is_reported_and_the_exit_status_is_1(self):
        status, output = self.run_script({**KEYS, **PROVIDER}, failing={"key-three": 401})
        self.assertEqual(status, 1)
        # The test keys are short, so they are fully masked (keys of 10 characters or fewer never show any part).
        self.assertIn("3. ………  FAILED", output)
        self.assertIn("request 3: ……… → ………  answered", output)

    def test_no_keys_configured(self):
        status, output = self.run_script(PROVIDER)
        self.assertEqual(status, 1)
        self.assertIn("No keys found", output)


if __name__ == "__main__":
    unittest.main()
