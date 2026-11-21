# Failure handling

## Requests and inputs

The HTTP API accepts JSON objects, checks content type and request size, and returns a JSON error with status 400 for validation failures. Unknown POST routes return 404. Unexpected API failures return a generic JSON error with status 500 when a response has not already started. The server logs the exception class without the exception message. Client disconnects do not trigger another response. Scenario, projection and simulation inputs are validated for required fields, ranges, finite numbers and valid dates before use. The browser displays request errors in the relevant form or notification and restores the submit control so the request can be corrected and retried. An unreadable server response gets a generic message.

## AI provider

Configure provider settings and keys in `.env`, then restart the server. A key supplied with a request is used alone. Otherwise configured keys rotate, and keys rejected with 401 or 403 or exhausted by a 429 are temporarily cooled down. `AI_MODEL_RETRY_SECONDS` controls the cooldown, defaulting to 60 seconds. Cooled keys remain eligible when no uncooled keys are available.

For a configured OpenAI-compatible endpoint, the server tries the configured model sequence after a 404, 429, server error, timeout, malformed response, or a 400 from a web-search request. Failed models are cooled down and become eligible again after the configured interval. When no alternative succeeds, the request receives an error describing the provider failure. Direct Gemini calls use the configured single model and return an error for malformed responses or provider failures. Check the key, model access, endpoint, and connection, then retry. Provider output is validated before it is used or cached.

If an extraction reports an invalid provider response, first check the protocol selected by `AI_BASE_URL`. Native Gemini extraction reads `candidates`. An OpenAI-compatible chat completion supplies `choices`. Once the server decodes the provider's JSON object, extraction validates its financial fields and selected variable IDs. An invalid scenario at that stage requires correcting the scenario. An interrupted connection during the provider read can trigger a retry on the next configured model.

## Cached answers

The AI answer cache treats missing, unreadable, or malformed entries as misses and requests a fresh answer. Valid answers are written to a temporary file and atomically moved into place. If the cache directory or write is unavailable, the validated answer is returned and temporary file cleanup is attempted. Repeated requests can then produce different answers until caching works again. Check permissions and free space for `AI_CACHE_DIR`, which defaults to `.local/ai-cache`. A process-local lock coordinates concurrent writes within one server process.

Life-event research uses PostgreSQL for presets and cached results. An unavailable cache yields an empty preset list. When `DATABASE_URL` is configured but the database is unavailable, research can still proceed with configured AI credentials, and a validated research result is returned even if the cache write fails. Failed research leaves the event without researched values, so projection assumptions supply defaults. Check `DATABASE_URL` and database availability to restore preset and cache access.

## Startup and database

`start.sh` creates the Python environment and `.env` if needed. It checks database connectivity, attempts to start the default local database, and exits with a message if it cannot connect. Server startup requires `DATABASE_URL` and initializes the database before accepting requests. Database initialization uses a transaction, so failed seeding does not leave partially imported data. Correct the connection or input files and retry startup.

## Verification

Run the suite from the repository root after installing dependencies.

```sh
.local/venv/bin/python -m unittest discover -s tests
```

PostgreSQL integration tests require `TEST_DATABASE_URL` and are skipped when it is absent. Point it at a test database before running the same command to include those tests. Focused cache and provider checks can be run with `-p test_ai_cache.py` and `-p test_api_keys.py` respectively.

To check the reported Gemini variable-selection failure and both response formats, run the mocked web-flow tests. They set their provider configuration explicitly and require no live API key.

```sh
.local/venv/bin/python -m unittest discover -s tests -p test_web.py
```
