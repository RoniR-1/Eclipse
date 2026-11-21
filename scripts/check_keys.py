"""Check the API keys configured in .env (AI_API_KEY_1, AI_API_KEY_2, ...).

Usage (from the repository root):

    .local/venv/bin/python scripts/check_keys.py            # test each key, then the rotation
    .local/venv/bin/python scripts/check_keys.py --rotation 8
    .local/venv/bin/python scripts/check_keys.py --rotation 0  # only test each key

1. Lists the keys that were loaded, masked to their first and last four characters.
2. Sends one tiny request with each key on its own and reports OK or the error.
3. Sends requests through the normal rotation and shows which key served each one,
   including any failover to the next key.

Each request is a few tokens, so a full run costs almost nothing. Exits with status 1 if a key
fails or no key is configured, so it can be used in scripts.
"""

import argparse
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from financial_twin import server  # noqa: E402

PROMPT = 'Reply with the JSON object {"ok": true}.'
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def mask(key):
    return f"{key[:4]}…{key[-4:]}" if len(key) > 10 else "…" * 3


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rotation", type=int, default=None, metavar="N",
                        help="requests to send through the rotation (default: two rounds of all keys; 0 to skip)")
    args = parser.parse_args(argv)

    server.load_env()
    keys = server.configured_api_keys()
    endpoint = server.os.environ.get("AI_BASE_URL", "").strip() or "Gemini (no AI_BASE_URL set)"
    models = server.os.environ.get("AI_API_KEY_MODEL", "").strip() or server.os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
    print(f"Provider: {endpoint}")
    print(f"Models:   {models}")
    print(f"Keys:     {len(keys)} loaded" + (f": {', '.join(mask(key) for key in keys)}" if keys else ""))
    if not keys:
        print("\nNo keys found. Fill in AI_API_KEY_1, AI_API_KEY_2, ... in .env and run this again.")
        return 1

    print("\nEach key on its own:")
    failed = 0
    for number, key in enumerate(keys, 1):
        try:
            server.request_json_with_key(PROMPT, SCHEMA, key)
            print(f"  {number}. {mask(key)}  OK")
        except ValueError as exc:
            failed += 1
            print(f"  {number}. {mask(key)}  FAILED: {exc}")

    rounds = 2 * len(keys) if args.rotation is None else args.rotation
    if rounds > 0:
        print(f"\nRotation ({rounds} requests):")
        real_urlopen = server.urlopen
        attempts = []

        def spy(request, timeout):
            header = request.get_header("Authorization") or request.get_header("X-goog-api-key") or ""
            attempts.append(mask(header.removeprefix("Bearer ")))
            return real_urlopen(request, timeout=timeout)

        with patch.object(server, "urlopen", spy):
            for number in range(1, rounds + 1):
                attempts.clear()
                try:
                    server.gemini_json(PROMPT, SCHEMA)
                    outcome = "answered"
                except ValueError as exc:
                    failed += 1
                    outcome = f"FAILED: {exc}"
                print(f"  request {number}: {' → '.join(attempts) or '(no attempt)'}  {outcome}")

    print("\nAll keys work." if not failed else f"\n{failed} check(s) failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
