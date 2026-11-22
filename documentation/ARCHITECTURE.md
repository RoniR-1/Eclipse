# Architecture

## High-level pattern

AI Financial Twin is a modular monolith. One Python process serves the static browser application, handles JSON requests, orchestrates provider and database access, and calls the simulation modules. PostgreSQL and the configured AI provider are external dependencies, while the application modules run together.

The calculation boundary is a layered, neuro-symbolic design. The non-deterministic layer interprets stories and proposes structured financial changes, optionally using grounded search. The deterministic layer validates those inputs and calculates monthly accounting results in Python and NumPy. Once the scenario, reviewed changes, dates and other inputs are fixed, the engines do not need the provider to recalculate them. Fresh extraction, exchange rates and public reference figures can change before inputs are frozen.

## Why this architecture

The modular monolith keeps deployment and local development straightforward. The HTTP server calls the calculation modules directly in one application process, while PostgreSQL supplies the shared reference data and event knowledge. This keeps the calculation code independent of HTTP and provider access, though the application components currently deploy together.

The AI layer lets users describe financial situations and decisions in their own words. Structured proposals give users a chance to inspect and edit assumptions, while deterministic accounting makes the resulting cash movements auditable and lets saved inputs be replayed without another provider call. Monthly checkpoints match the frequency of the supplied cash balances. Branching at a decision date lets users hold the preceding path fixed and inspect that decision's effect.

The two engines serve different workflows. `engine.py` retains balance-sheet snapshots so a user can branch from a selected checkpoint. `projection.py` resolves a structured profile and named events into timelines for baseline comparisons, using labelled defaults where inputs are missing. Maintaining both calculation paths requires their accounting behavior to be verified independently, with shared loan arithmetic in `debt.py`.

## System and data flow

```text
Browser UI (web/) <---- static files and JSON ----> HTTP server (server.py)
                                                        |
    +---------------------------------------------------+
    |
    +-- Story or event --> LLM extraction / proposal <--> AI answer files
    |                        |     |
    |                        |     +--> provider (grounded search when available)
    |                        v
    |                      Browser reviews scenario or edits building blocks
    |                        |
    +-- Reviewed inputs <----+
    |       |
    |       +--> engine.py (via branching.py for proposals) --> monthly nodes
    |
    +-- Profile + named events --> projection.py --> baseline and path timelines
    |
    +-- Named milestone --> event_resolver.py <--> PostgreSQL life_event_knowledge
    |                          +--> grounded search on cache miss
    |
    +-- Currency context --> currency.py <--> memory and file FX caches
                                +--> Frankfurter reference rates

Startup: fixture CSVs --> database.py --> PostgreSQL
Results: monthly nodes or path timelines --> HTTP server --> Browser UI
```

`server.py` serves `web/` using `ThreadingHTTPServer`. The browser keeps the interactive node tree and sends JSON snapshots with requests. Story extraction uses `POST /api/extract`, then `POST /api/simulate` runs the accepted scenario. For a branch, `POST /api/branch/propose` returns editable building blocks and sources to the browser. After review, `POST /api/branch/apply` simulates them, or `/api/branch/append` adds them to the current path and recalculates its future. The compatibility endpoint `/api/branch` performs an AI interpretation and simulation in one call without that review step. `POST /api/project` runs the separate structured profile workflow, with `GET /api/schema` describing its input contract.

## Module responsibilities

| Module | Inputs, outputs and role |
| --- | --- |
| `engine.py` | Validates a CHF scenario and creates a root balance-sheet node. `simulate` advances monthly cash, assets, debt, inflation, goals and funding feasibility, returning JSON-compatible nodes linked to their parents. Children copy state and carry scenario snapshots, so branches can start from a checkpoint. The dictionaries are treated as immutable snapshots by the simulation, although Python does not enforce immutability. NumPy supports the calculations and `debt.py` supplies loan arithmetic. |
| `branching.py` | Normalizes proposed recurring changes, one-off amounts and purchases, then transforms reviewed blocks into engine phases and branch nodes. Keeping this transformation separate lets the engine consume explicit financial inputs without calling the AI. |
| `projection.py` | Resolves a profile and named decision events into rule-based monthly arrays, timelines, comparisons, goals and sensitivity bands. It fills missing inputs using rules and the aggregates in `defaults.json`, then runs its own recurrence with the shared loan arithmetic in `debt.py`. Its returned `scenario_record` freezes resolved inputs for subsequent replay through `/api/project`. |
| `uncertainty.py` | Re-simulates an engine node tree under shifted assumptions and returns conditional ranges and rankings. It uses the engine's calculation rules for each scenario. |
| `currency.py` | Supplies currency metadata and converts recognized input amounts to CHF. It retrieves CHF-based rates and exposes the rate and source for display conversion. |
| `loaders.py`, `synthetic_project.py` and `comparison.py` | Load fixture profiles and starter scenarios, attach input provenance, adapt synthetic profiles for projections and build the Alexia comparison. Dataset handling stays outside the calculation engines. |
| `event_resolver.py` | Resolves supported named life events from PostgreSQL knowledge or grounded provider search. It validates returned parameters and source URLs before caching usable results. The server passes resolved values into the projection or extraction inputs. |
| `server.py` | Validates HTTP payloads, coordinates extraction, providers, caches, simulation, node edits and reports, and serves the UI. `engine_report.py` and `report.py` assemble comparison output from calculated paths. |
| `database.py` | Creates PostgreSQL tables and indexes, seeds public event knowledge, and imports bundled fixture CSVs when the fixture tables are empty. `scripts/seed_database.py` also invokes initialization. |
| `reproduce.py` and `scripts/reproduce.py` | Record and replay engine runs, ordered edits, branches and retained nodes. The script compares recorded values with reconstructed values without a database or AI provider. |

## Decisions and trade-offs

**CHF as the internal currency.** Scenarios and accounting use CHF, matching the Swiss reference data. `currency.py` converts recognized amounts before extraction and the UI converts amounts for display. Rates can change between new requests, so the response includes rate context. Live rates are cached in memory and in `.local/exchange-rates.json`; saved and built-in rates provide offline fallbacks.

**Review before the normal branch simulation.** The UI presents AI-proposed building blocks, assumptions and sources for editing before applying them. The server validates the submitted plan and the engine calculates from that plan. The compatibility `/api/branch` endpoint does not enforce this gate, so API consumers that need review should use the propose and apply endpoints.

**Resilience and repeatability.** Configured provider keys rotate, with cooldown and retry behavior for key-specific failures, and provider model fallback is supported. Validated AI answers are cached as files keyed by request kind, normalized prompt, schema and prompt version. Event references have a separate PostgreSQL cache with expiry. At startup a transaction-scoped PostgreSQL advisory lock serializes schema initialization and fixture seeding; fixture import occurs only when all relevant tables are empty. File caches are local to the application filesystem and can be recreated. They stabilize repeated requests while available, but fresh provider responses, FX rates and event references can differ.

**Offline replay.** A downloaded engine run contains the scenario or seeded test inputs, operations and expected retained values. `scripts/reproduce.py` rebuilds the tree and checks recorded values exactly, reporting Python and NumPy versions because floating-point results can vary across environments. Projection `scenario_record` replay is a separate contract.

## Verification and testing

`tests/test_accounting_invariants.py`, `tests/test_engine.py`, `tests/test_projection.py` and branch tests check that cash changes reconcile with recorded flows, assets less liabilities equal net worth, and a fork preserves its parent’s state. `tests/test_reproducibility.py` covers seeded runs, deterministic IDs, JSON round trips, edits, deletions and tampering, while the committed sample can be checked with `.local/venv/bin/python scripts/reproduce.py examples/sample-run.json`. Currency, uncertainty, loaders, provider cache, key rotation, event resolution and database behavior have dedicated tests. `tests/test_http_api.py`, frontend tests and `tests/web/app.test.mjs` cover the HTTP and browser-facing paths. PostgreSQL integration checks use a disposable `TEST_DATABASE_URL`.

`start.sh` prepares the local environment, checks or starts the configured local PostgreSQL instance and launches the server. `railway.toml` runs the same server on the supplied port and uses `/` for its health check. The full commands and API contracts are in `README.md` and `TECHNICAL.md`.

## Scaling path

JSON-compatible node snapshots carry the state needed for a calculation, which gives a future deployment a basis for distributing requests without rewriting the engine. Cache and database access currently live in application modules and use local files and PostgreSQL directly. Scaling across instances would require shared or replaceable cache storage and coordination appropriate to that deployment, while the core calculations can continue to operate on explicit inputs.
