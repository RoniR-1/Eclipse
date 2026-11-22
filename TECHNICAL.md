# Technical documentation

## Server configuration and usage

To configure the AI provider, copy `.env.example` to `.env`, set `AI_API_KEY_1`, `AI_BASE_URL`, and `AI_API_KEY_MODEL`, and restart the server. Put model names in preference order on one line, separated by commas, such as `AI_API_KEY_MODEL=model1,model2,model3`. When a model is unavailable, rate-limited, or times out, the server tries the next model and waits 60 seconds before trying the failed model on a later request. Set `AI_MODEL_RETRY_SECONDS` to change that interval. If all models are cooling down, the server tries them in order.

To spread requests over several API keys, set `AI_API_KEY_1`, `AI_API_KEY_2`, `AI_API_KEY_3` and so on; any number of keys is read in numeric order, and gaps or blank values are skipped. Each AI request starts with the next key in turn. If a key is rate-limited (HTTP 429) or rejected (401 or 403), the same request is retried with the next key, and that key rests for `AI_MODEL_RETRY_SECONDS` before it is used again; if every key fails, the last error is shown. Network errors and invalid requests are not retried with other keys, since another key would not help. The single `AI_API_KEY` (or `GEMINI_API_KEY`) still works and joins the rotation if set. The website has no key settings, so keys are only configured in `.env`. The server authenticates directly with the configured provider. The browser receives only a key-presence flag from `/api/config`.

Describe your finances and the decision you want to explore, using CHF for monetary amounts. Select a 5-, 10- or 20-year horizon and submit. The server sends the statement to the configured provider to extract a starting scenario and choose three to five relevant controls. Review the assumptions, adjust the sliders, and choose **Simulate Future** to project the monthly timeline with the Python engine.

The server also exposes the [projection engine](#projection-engine-user-entered-profile), which projects a user's own starting numbers with rule-based decisions: `GET /api/schema` returns the input fields, event types, defaults and ranges, and `POST /api/project` returns the baseline and decision paths. The homepage also exposes deterministic comparisons. API clients can supply custom event lists, synthetic profile IDs, or a frozen scenario record.

## PostgreSQL data

The server requires `DATABASE_URL`. The provided Docker Compose database listens on localhost port 5433 and stores data in the `financial_twin_data` named volume, which survives application and database restarts. A local PostgreSQL instance or hosted PostgreSQL database can also be used by setting its URL in `.env`. Start the database before starting the server. The existing local PostgreSQL cluster in this workspace can instead be started with `/usr/lib/postgresql/16/bin/pg_ctl -D .local/postgres -l .local/postgres.log -o '-p 5433 -k /tmp' start` and stopped with `/usr/lib/postgresql/16/bin/pg_ctl -D .local/postgres stop`. Use either this local cluster or Compose at a time, since both use port 5433.

On startup, PostgreSQL tables are created if needed. If every application data table is empty, the eleven bundled fictional seed tables in `fixtures/demo` are loaded in one transaction under an advisory lock. If any table contains data, the import is skipped; subsequent starts do not read the CSVs. A failed import rolls back, and concurrent startups cannot duplicate the seed. To deliberately reset data, empty all application tables or create a new database; removing the Compose volume permanently deletes the stored data. The catalog and library retain separate source tables. Most records link to profiles, accounts, or scenarios through foreign keys; asset account references remain unconstrained because some investment accounts are absent from the accounts CSV.

To run the PostgreSQL integration test against a disposable schema, set `TEST_DATABASE_URL` to a database where the user can create schemas, then run `.local/venv/bin/python -m unittest discover -s tests -v`.

### Optional organizer-data audit

The default seed uses the packaged files. To audit a separately authorized copy of the full challenge, set `FINANCIAL_TWIN_DATA_ROOT` to its root and use a fresh database. Preserve `Data/*.csv` and `docs/scenario-library-120.csv`. Extract `Data/synthetic-transactions.zip` so the transaction table is at `Data/synthetic-transactions/synthetic-transactions.csv`. The root also selects the source for synthetic profile-ID projections. The canonical demo always reads its own packaged assumptions. Running the optional archive tests against the original collection requires placing it in the challenge directory expected by those tests.

## Simulation engine

This Python package models monthly financial decisions for the LauzHack challenge. It contains two engines that share the same monthly cash recurrence:

| Module | Purpose | Used by |
| --- | --- | --- |
| `financial_twin/engine.py` | Phase-based balance sheet with branching checkpoint nodes, inflation, investment allocation and debt. Its inputs and nodes are JSON-compatible so a web backend can persist them and a browser can render paths without reproducing financial calculations. It uses NumPy for asset and debt calculations. | `/api/simulate`, `/api/branch` |
| `financial_twin/projection.py` | Projection of a user-entered profile with six rule-based decision types, dataset defaults, metrics, uncertainty bands and baseline comparisons. Standard library only. | `/api/schema`, `/api/project` |

## Challenge materials

Stefania Stan’s challenge brief describes a fictional student comparing work with study. Historical sample statistics below describe the original challenge collection. The submission ships its own demonstration fixtures in `fixtures/demo`. See [data and software attribution](evaluation/attributions.md).

The original challenge collection contained 1,200 synthetic profiles, 1,200 accounts, 2,399 asset snapshots, 283 liability snapshots, 1,500 goals and 28,800 monthly cash reconciliations. The 120 catalog entries describe decisions. The 240 scenario assumption rows cover only a subset of profile/scenario combinations. The transaction and life-event CSVs are also loaded. Net monthly transactions alone cannot identify the underlying income and expense categories.

Profiling the data showed three limits that shape the projection engine:

- **Life events are placeholders.** The 600 rows in `life-events.csv` cover only profiles P00001–P00200, three events each, with types assigned at random and only three dates (2025-09-01, 2026-06-01, 2026-09-01). Every `cash_effect_chf` is empty and every status is `proposed_not_executed`, so the transaction ledger never reflects them. The file supplies the five event types, not their financial effects.
- **Only S001 has parameters.** `scenario-assumptions.csv` holds `study_duration_years` and `annual_tuition_chf` for the master's scenario only, for P00001–P00120. Their medians (24 months, CHF 4,800 per year) are the education defaults.
- **Spending barely depends on the profile.** Across all 1,200 profiles, other fixed costs are about CHF 709 per month (standard deviation about 29) and discretionary spending about CHF 745, almost regardless of income, household or employment. Income, household and employment explain about 4% of the variation in fixed costs and 17% in discretionary spending. Only the annual bonus follows income (about 2.8% for employed people). A model trained on this data would learn near-constants, so the engine uses medians and means as labelled defaults instead of a fitted model.

The transactions reconcile exactly with `synthetic-cash-balances.csv`: opening cash plus each month's net transactions equals closing cash in all 28,800 rows.

The bundled ten-year reference uses two CHF 2,500 tuition charges. The generalized education default is CHF 4,800 annually, and the five-year homepage story explicitly specifies CHF 8,000 each year. Preserve the selected convention when comparing outcomes.

## Modelling choice

Use a deterministic monthly balance sheet and branch at decision dates. It fits the frequency of the supplied cash balances, makes individual cash movements auditable and lets a user hold everything else fixed when changing one decision. A portfolio allocation, return assumption or tuition shock can be varied in a separate path. The supplied snapshots do not calibrate market-return distributions, correlations or personal risk preferences, so attaching probabilities to these paths would require additional evidence.

Keep net pay, recurring expenses and one-off costs separate. Debt payments reduce cash, interest accrues on outstanding debt, and principal repayment reduces the liability. Transfers from cash into investments change liquidity while preserving net worth at the instant of transfer. Each monthly checkpoint therefore needs both a balance sheet and a record of that month's flows. A negative cash balance measures the funding gap under the chosen assumptions. A user can inspect that gap even when the ending net worth is positive.

## Run and branch

The model applies these rules once per month, in this order.

| Quantity | Calculation and interpretation |
| --- | --- |
| Income and living costs | Base monthly income and base monthly expenses × the price index. The index starts at 1 and grows by `(1 + annual_inflation) ** (1/12)` per month, so phase amounts are in today's money and wages keep pace with prices. Dated one-off costs and loan payments are nominal CHF amounts and are not indexed. |
| Debt | Interest = opening principal × nominal annual loan rate / 12. Payment is capped at principal plus interest. The remaining amount becomes next month's principal. |
| Asset growth | Each opening holding earns `(1 + annual_return) ** (1/12) - 1`. New contributions start earning returns the following month. |
| Operating cash flow | Net income − living costs − dated costs − debt payments. |
| Reserve and investment | Reserve = living costs × emergency months × safety priority. Invest the lesser of (positive operating surplus × savings rate) and cash available above the reserve, multiplied by risk tolerance × (1 − safety priority). Negative available cash contributes zero. Here savings rate is the fraction of surplus eligible for investment. Unallocated surplus stays in cash. |
| Closing state | Closing cash = opening cash + operating flow − investment contribution. Net worth = closing cash + noncash asset values − outstanding debt. |
| Exposure | Track the unfunded cash shortfall, investment value and investment share. The annual CHF volatility measure sums holding value × assumed annual volatility. This is a sensitivity measure assuming perfectly correlated moves. |

Savings rate, risk tolerance and safety priority run from 0 to 1. These are explicit behavioural assumptions. Default savings rate, inflation and returns are zero to reproduce the starter cash paths. A user can supply a portfolio return and volatility assumption independently of their willingness to take risk. The model has one currency (CHF), no automatic borrowing or liquidation, and no taxes beyond those already reflected in net income. It uses floating-point CHF for projections and leaves cent rounding to display.

From the workspace root, run this example in Python. Month 0 starts on the opening date. Node 12 represents the state after twelve monthly transitions. Each child points to its parent, so the web app can combine paths into a graph keyed by node ID.

```python
import json
from pathlib import Path
from financial_twin import create_root, simulate
from financial_twin.loaders import load_starter_scenario

kit = Path("ai-financial-twin-lauzhack-2026/starter-kit")
scenario = load_starter_scenario(
    kit / "sample_profile.json", kit / "scenario_config.json",
    "Start working", start_date="2025-01-01",
)
root = create_root(scenario)
first_year = simulate(root, 12)
checkpoint = json.loads(json.dumps(first_year[-1]))
baseline = simulate(checkpoint, 12)
move = simulate(
    checkpoint, 12, branch_id="move",
    scenario_updates={
        "phases": [{"start_month": 12, "end_month": None,
                    "monthly_income_chf": 3700, "monthly_expenses_chf": 2900}],
        "one_off_costs": [{"month": 12, "amount_chf": 6500, "description": "Relocation"}],
    },
)
graph = {n["id"]: n for n in [root, *first_year, *baseline, *move]}
print(baseline[-1]["state"]["cash_chf"], move[-1]["state"]["cash_chf"])
```

Both paths start year two with CHF 22,400. Continuing to work adds CHF 14,400 to cash. Moving adds CHF 9,600 before the CHF 6,500 relocation cost. Their year-two closing balances are CHF 36,800 and CHF 25,500. The example uses the starter's illustrative amounts and chooses January 2025 as the opening date.

For a CSV-backed opening state, use `load_synthetic_profile(data_dir, profile_id, monthly_expenses_chf=...)`. Supply total recurring living costs, including rent and excluding loan payments. The profile provides reference net income and rent but lacks a complete spending budget. Cash is repeated in the profile, account and asset files and enters the opening balance only once. Investment growth defaults to zero. A zero scheduled loan payment leaves accrued interest unpaid. The goals table can inform a user's chosen cash reserve. The engine does not infer preferences from a profile's age or employment.

## Branching from a milestone

Selecting a milestone opens **What if something changed?**. A user picks an event type, describes the event in their own words, or both. The AI turns the request into editable building blocks and sets every number; they are shown in a review card and only simulated after the user confirms.

| Block | Meaning |
| --- | --- |
| Monthly change | Income or expense change per month, with a start offset and a duration (0 = permanent), for example −3,000 income for 24 months, then +1,000 from month 24 |
| One-off amount | A single amount at an offset: positive for a cost, negative for money received (booked as one month of extra income) |
| Purchase | An asset bought at the branch point with an optional loan. The server enforces price = loan + down payment, so a purchase never creates or destroys wealth; only the purchase costs reduce net worth. Annual value change models appreciation or depreciation. A rent deposit is a purchase of kind `deposit`. |
| Investment change | Revalues existing investments at the branch point (0.7 = −30%) |

Every block carries a source (`user`, `dataset`, `assumption` or `ai_estimate`), and editing a value marks it `user`. The server returns warnings when the down payment was corrected, a loan payment is below its interest, or upfront costs exceed the available cash.

The event types in `financial_twin/branching.py` (Study, Career change, Relocate, New home, Unexpected expense, Have a child, Buy property, Market shock, Go part-time) carry no numbers. Each one gives the AI guidance on what to model, for example tuition for the study period, reduced income while studying and the graduate salary afterwards, plus an example of useful details such as the programme and country. The AI first asks for web search, so it can look up current figures such as tuition or local rents, and returns the pages it used as `source_urls`; the review card links them. If the provider rejects search, the request is repeated without it. The prompt includes the dataset reference values (study length, tuition, unexpected-expense amount, median rent), which the AI marks with source `dataset` when it uses them.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/branch/presets` | Event types: id, label and an example of useful details |
| `POST /api/branch/propose` | `{parent, preset?, event?}` with at least one of them → `{proposal, origin}`. Uses a cached proposal when available, otherwise asks the provider. |
| `POST /api/branch/apply` | `{parent, event, months, variables, proposal}` → branch nodes. Deterministic; the first node stores the applied proposal as `branch_proposal`. |
| `POST /api/branch/append` | `{nodes, node_id, event, proposal, sequence?, months?}` → the recalculated points. Adds the event to the selected point's path instead of branching; see below. Deterministic. |
| `POST /api/branch` | One step (propose and apply without review), kept for compatibility |

`/api/branch/apply` splits the engine's phases at every block boundary, so temporary changes end on time and their start and end appear as milestones. The random test scenario, which must work without the AI, builds its branches from fixed sample events (`SAMPLES`); these are not offered to users. `tests/test_branching.py` checks durations, purchase accounting, money received, warnings, every preset and the AI and no-AI paths.

A reviewed building-block branch continues the selected node’s balance sheet and scheduled commitments. Recurring changes split the inherited phases at their effective start and end. The fork record identifies inherited, added and replaced commitments.

### Adding an event to a path

The review card also offers **Add to this path**. Instead of starting a branch, the event is added to the path the selected point is on, and every later month of that path is simulated again with it:

- **Effect:** the event takes effect from the selected point, like a branch: monthly changes, one-off amounts, purchases with their loans and the investment change. It is added on top of what the path had already planned, so the path keeps its own later phases and costs.
- **Later events stay.** Events further along the path (purchases, loans, other added events) are applied again on top of the new figures, using the same re-simulation as **Edit this point** (`resimulate_node` in `financial_twin/server.py`).
- **Branches that split off later** get the parts of the event that had started by the time they split off. Since a branch replaces the plans made after its fork, a change or one-off scheduled after that is not theirs. Their own event is applied on top.
- **Branches that split off at the selected point itself** are alternatives to the event and stay unchanged. So do earlier points and every other path.
- **IDs stay:** the recalculated points keep their IDs, so nothing else on screen needs to change. The point after the selected one shows the event's name as a milestone and lists what it assumes under **Event added here**. Several events can be added at the same point; their effects add up.
- **Last point:** if the selected point is the last one on its path, the path is extended by the period chosen in the card.

Each addition is recorded in the run file's `branches` list as `kind: "append"` with its proposal, so replay repeats it in order. `tests/test_append_event.py` checks the recalculated future, accounting identities on every month, later branches, extensions, two events at one point, an earlier addition keeping a later one, a later edit keeping the addition, exact replay, and the HTTP endpoint.

## Uncertainty range

**Show range** in the results view adds, for the selected path only, a band to the trajectory chart, a ±CHF badge (half the width of the range) on each timeline edge and on every tree node and edge, the range on each timeline card, and a table in the node details (pessimistic, central and optimistic net worth and cash, in CHF and as % of the central value). Badges use CHF rather than %: a percentage of the central value shrinks whenever net worth grows faster than the uncertainty and explodes near zero, while the CHF range never shrinks along a path. The range is off by default and is not a branch: it adds no nodes and cannot be branched from.

`POST /api/uncertainty` takes the nodes on screen and re-simulates the whole tree month by month, the way `/api/edit-nodes` does, with adjusted inputs (`financial_twin/uncertainty.py`). Every monthly amount is split into today's situation (the first phase of the starting point) and the change since (later phases and events):

| Input | Pessimistic / optimistic |
| --- | --- |
| Today's income | held fixed |
| Today's living costs | +5% / −5% |
| Event effects | each change since today 15% less / more favourable |
| One-off costs | +15% / −15% |
| Investment returns | − / + volatility ÷ √(horizon in years), the uncertainty of an average return |
| Inflation | ±1 percentage point; the side that lowers final net worth counts as pessimistic |

The pessimistic run moves every input to its unfavourable side at once and the optimistic run to its favourable side, to construct two stress scenarios. Neither line estimates a probability or bounds every possible outcome. Ranges are centred on the values shown: low = shown + (pessimistic − unadjusted re-simulation), so the band never drifts from the timeline. The response also lists, per branch, the first month cash turns negative in the pessimistic case and the inputs ranked by their effect on the branch's final net worth (each moved on its own). The starting-cost stress applies even when someone has entered that value. `tests/test_uncertainty.py` checks that the unadjusted re-simulation reproduces every node exactly, hand-calculated driver effects, widening over time and input validation.

## Goals scorecard

**Goals** (in the results header and the tree's top bar) opens a panel where users describe their goals in their own words or add them in a form, then rank every path on screen with **Find the best path**. Goals are kept in the browser (`localStorage`).

The AI does one thing: `POST /api/goals/parse` turns the user's sentence into structured goals (`financial_twin/goals.py`, `GOALS_SCHEMA`), through the AI cache, for the user to review and edit. Nothing is trained, and the AI never ranks. `POST /api/goals/score` is deterministic: it takes the nodes on screen and the goals, computes the uncertainty range (`financial_twin/uncertainty.py`) and checks every path:

| Goal type | Met when |
| --- | --- |
| `cash_by_date` | cash at the goal month ≥ target (e.g. a down payment) |
| `net_worth_by_date` | net worth at the goal month ≥ target |
| `never_short` | cash never negative over the path |

Per goal and path the response gives the gap (value − target), the closeness (value ÷ target capped at 100%, or the share of months with cash ≥ 0) and a status: *robust* (met even in the pessimistic range), *fragile* (met, but not pessimistically), *within reach* (met only in the optimistic range) or *missed*. A path's score is the priority-weighted mean closeness (high 3, medium 2, low 1); ties go to more robust goals, then higher final net worth. Goals are judged independently, so two cash goals do not share one pot of money, and a goal dated after a path ends is judged at its last month. `tests/test_goals.py` checks hand-calculated statuses and gaps, the weighting, ranking, validation and the parsing step.

## Check the implementation

Run `.local/venv/bin/python -m unittest discover -s tests -v` from the workspace root. Historical organizer-data audits additionally use a local, authorized copy of the challenge files. This also runs the browser-logic tests when Node.js is installed; run them on their own with `node --test tests/web/app.test.mjs`. The starter's original tests can also be run from `ai-financial-twin-lauzhack-2026/starter-kit` with `python3 -m unittest -v`.

| Test file | What it checks |
| --- | --- |
| `test_engine.py` | Checkpoints, branch isolation, transfers, debt, inflation and invalid phases in the simulation engine. |
| `test_accounting_invariants.py` | The validation checklist's identities on every month of 40 seeded timelines with branches: cash rolls forward by net cash flow, net worth equals assets minus debt, moving cash into investments leaves net worth unchanged, debt payments split into principal and interest, and steps are one calendar month. Also the checklist's boundary cases: zero income, zero interest, paid-off loans, higher tuition, annual-to-monthly conversion and negative returns. |
| `test_branching.py` | Life-event branches (one-off costs, recurring changes, financed purchases, market crashes, phase splitting, branch-specific assumptions), rejection of invalid AI output and requests before the AI is called, the Test button's data, narratives and `.env` loading. |
| `test_http_api.py` | Every endpoint through the real HTTP handler, JSON 400 errors for malformed requests, 404s, no caching, and that files outside `web/` are not served. |
| `test_web.py` | AI request format, model fallback and cooldown, slider handling and nested branches. |
| `test_reproducibility.py` | Identical results from identical inputs, seeds, deterministic branch IDs and exact replay of run files. |
| `test_ai_cache.py` | Same input, same numbers: with a fake AI that answers differently every time, the same story or branch event still gives identical numbers, also through the HTTP API, after a restart and under concurrent requests; invalid answers are not cached. |
| `test_currency.py` | Currencies: amounts written in other currencies (symbols, codes, words, `k`/`million`, Swiss and European separators) are converted to CHF exactly, CHF amounts and bare numbers are left alone, live rates are reused, offline falls back to saved and then built-in rates, the AI gets the story, branch events and branch proposals in CHF, and unsupported currencies are rejected. No test uses the network. |
| `test_append_event.py` | Adding an event to an existing path: the later months are recalculated with it, earlier points and alternatives at the same point stay, later branches keep their own event and only get what had started before they split off, accounting holds every month, the last point extends the path, events add up, later edits keep them, and run files replay them exactly. |
| `test_api_keys.py` | Taking turns across `AI_API_KEY_1`, `AI_API_KEY_2`, …, failover on rate limits and rejected keys, rest periods, and `scripts/check_keys.py`. |
| `test_event_resolver.py`, `test_event_cache_postgres.py` | Reference figures for named institutions and places: cache hits and refreshes, aliases, grounded citations and failed lookups (the PostgreSQL test needs `TEST_DATABASE_URL`). |
| `test_frontend.py`, `web/app.test.mjs` | Browser logic: branch and Original-path deletion, cutting a path from a later point (with the branches that split off after it), the confirmation flow, the tree index, timeline milestones, health flags, number formatting, HTML escaping, and that a run file written by the browser replays exactly in Python. |
| `test_projection.py`, `test_loaders.py`, `test_database.py` | The projection engine, the challenge data loaders and the PostgreSQL seed (needs `TEST_DATABASE_URL`). |

`tests/test_projection.py` runs without the challenge data, except for one ledger replay. That test feeds the monthly income and spending of profiles P00001–P00050 from `synthetic-transactions.csv` through the projection recurrence and requires every month to match `closing_cash_chf` in `synthetic-cash-balances.csv`. It looks for the data in `ai-financial-twin-lauzhack-2026-main/Data` or `ai-financial-twin-lauzhack-2026/Data` inside the repository, or in `../ai-financial-twin-lauzhack-2026-main/Data`, and is skipped when none exists.

## Currencies

The assumptions page has a **Currency** menu with CHF, EUR, USD, GBP, JPY, CNY, CAD, AUD, SEK, NOK, DKK, PLN and INR, and the timeline and full-tree views have a compact one next to their buttons. All menus stay in sync. Switching redraws what is on screen in the new currency and keeps the values set so far. The choice is remembered in the browser, and the next story is read with it: amounts written without a currency count as that currency, which the assumptions page's ⓘ states.

The engine always calculates in CHF, because the Swiss reference data is in CHF. Another currency is a view on the same numbers:

- **Shown amounts** are converted from CHF: the timeline, charts, summaries, the inspector, path reports, branch proposals and server-written text that quotes CHF amounts.
- **Typed amounts** are read in the chosen currency and converted to CHF before they are sent: assumption text boxes, node edits and the building blocks of a branch proposal.
- **The story and branch events** are converted on the server before the AI sees them. An amount written with a currency (`€15,000`, `4'200 EUR`, `50k euros`, `£1.5m`, `USD 20,000`) is rewritten as CHF in code, for example `CHF 13,835.09 (written as €15,000)`, so the AI does no arithmetic on it. For amounts without a currency, the prompt states the chosen currency and the exact rate. `$`, `¥` and `kr` mean the chosen currency when it uses that symbol; otherwise `$` is USD, `¥` is JPY, and `kr` is left to the AI. The assumptions page lists every conversion it made.

Rates are the European Central Bank reference rates from the free Frankfurter service (`https://api.frankfurter.dev`, no key; only the base currency is sent). They are kept for 6 hours and saved in `.local/exchange-rates.json`. If the service cannot be reached, the saved rates are used, and failing that, built-in rates of 2026-11-20 in `financial_twin/currency.py`; offline, the service is tried again after 5 minutes. Hovering over a currency menu shows the rate, its date and its source, and `GET /api/currencies` returns them.

Run files keep every number in CHF and record the display currency and its rate under `display_currency`, so replays are unaffected. The AI cache key includes the converted text and the rate: the same story in the same currency gives the same numbers for as long as the rate is unchanged. Comparison API amounts carry explicit CHF units.

## Same input, same numbers

A saved scenario, start date, horizon, reviewed changes and model implementation determine the replay. Fresh natural-language extraction can vary. The extraction date is assigned when the answer is validated. FX rates and public event references can also change.

The server hashes a whitespace-normalized prompt, schema, request kind and prompt version for its AI response cache. An unchanged cache key can reuse a validated answer across restarts, model changes and key rotation. A new prompt, currency conversion or cache deletion can produce new assumptions. The cache contains structured answers which can include sensitive amounts and text. The application sets no expiry on these files. Operators can remove the relevant entries from `AI_CACHE_DIR` (default `.local/ai-cache`). PostgreSQL caches named milestone lookups separately. Expiry limits reuse and does not itself erase a database row. Downloaded run files remain under the recipient’s control.

## Reproduce a run

Every result can be recomputed exactly from a saved run file, without the AI provider or the database.

1. Check the committed sample: `.local/venv/bin/python scripts/reproduce.py examples/sample-run.json`. It prints the source, horizon, units and software versions, then `REPRODUCED` when all 1,143 recorded values match exactly (exit status 0), or the differing values (exit status 1).
2. Reproduce your own run: in the website, choose **Download run file** in the horizon summary below the timeline, then run the same command on the downloaded file.

A run file records everything the calculation needs:

| Field | Contents |
| --- | --- |
| `source`, `seed`, `start_date` | `simulate` for a story-based run; `test` for the **Test** button's random data, which is regenerated from its seed. The seed is shown when the test data is generated. |
| `scenario`, `variables`, `years`, `units` | The engine inputs for the original path: the scenario the AI extracted, the slider values, the horizon, CHF and monthly steps. |
| `operations` | Ordered successful node edits, branches and events added to an existing path (`append`). Replay applies each against the state that existed at that step. Older files use the branch list. |
| `branches` | Every branch in creation order, with the change the AI produced for it and a sequence number that makes its node IDs deterministic. |
| `kept_node_ids`, `expected` | The nodes still on screen after any deletions, and their cash, net worth, total assets, total debt and net cash flow. |
| `environment` | The Python and NumPy versions that produced the results. |

The AI steps are the only nondeterministic part: asking the model again can return a different scenario or branch change. The run file therefore stores their outputs, and everything after them is deterministic. Replaying on different Python or NumPy versions is reported, since floating-point results could then differ.

`tests/test_reproducibility.py` checks that identical inputs give identical results, that test data reproduces from its seed, that branch IDs are deterministic, that a run file replays exactly after a browser-style JSON round trip, that deleted branches stay deleted, that a tampered value is detected, and that the committed sample still reproduces. If the engine is changed on purpose, regenerate the sample with `scripts/reproduce.py --write-sample examples/sample-run.json`.

## Projection engine (user-entered profile)

`financial_twin/projection.py` projects the user's own starting numbers rather than a dataset profile. The projection starts at the explicit start month, synthetic snapshot month, or current month (month 0), and point *k* of a timeline is the state after *k* monthly transitions. The paths are conditional projections under stated assumptions, not forecasts.

### Inputs

Only four profile fields are required. Every other value is filled in when missing, and every resolved value carries `source: user | dataset | assumption` so the interface can show where each number came from.

| Field | Default when missing | Source |
| --- | --- | --- |
| `age`, `cash_chf`, `monthly_net_income_chf`, `monthly_rent_chf` | Required | user |
| `employment_status` | `employed`; selects the dataset defaults below | assumption |
| `household_type` | `single`; context only, not used in calculations | assumption |
| `other_fixed_costs_chf` (insurance, utilities, transport, telecom) | Median by employment status, about CHF 710 | dataset |
| `discretionary_chf` (groceries, restaurants, retail, leisure, pharmacy, travel) | Median by employment status, CHF 629 for students to CHF 769 | dataset |
| `irregular_costs_chf` | Mean monthly unexpected expenses, about CHF 66 | dataset |
| `bonus_share_of_income` | Mean bonus share, 2.8% for employed and self-employed, 0 otherwise | dataset |
| `investments_chf`, `investment_annual_return` | 0 | assumption |
| `retirement_age` | 65 (Swiss AHV reference age) | assumption |
| `pension_share_of_income` | 0.6 of the last income | assumption |
| `debts[]` | `{name, principal_chf, annual_interest_rate, monthly_payment_chf}` | user |
| `goals[]` | `{name, target_chf, in_months or date, priority: high, medium or low}` | user |

The dataset defaults live in `financial_twin/defaults.json`, and ordinary user-entered projections read that aggregate file. Synthetic profile-ID projections read the bundled snapshot CSVs. Regenerate the file with `python scripts/build_defaults.py path/to/Data` after unzipping `synthetic-transactions.zip`.

### Decisions

A scenario is a named list of events `{"type", "in_months" or "date": "YYYY-MM", "params"}`. Events are applied in date order and can be combined freely. Parameters left out take these defaults:

| Type | Effect | Parameters and defaults |
| --- | --- | --- |
| `education` | Adds tuition/12 to fixed costs and scales income while studying, then sets the graduate income | `duration_months` 24 and `annual_tuition_chf` 4,800 (dataset); `income_share_while_studying` 1.0 for students, 0.2 otherwise; `income_after_chf` current monthly net income × 1.15, rounded to CHF |
| `career_change` | Scales income during a gap, then applies a percentage change or a new absolute income | `gap_months` 3; `income_share_during_gap` 0; `income_change_pct` 0.15; `new_income_chf` none; `one_off_cost_chf` 0 |
| `relocation` | One-off moving cost, then scales rent, income and other living costs | `moving_cost_chf` 1,500 (illustrative general default); `rent_change_pct`, `income_change_pct` and `cost_of_living_change_pct` 0 |
| `housing_change` | Sets the new rent and moves the rent deposit from cash into a locked asset, so liquidity falls but net worth does not | `new_rent_chf` current rent; `deposit_months` 3 (illustrative default); `moving_cost_chf` 1,500. The refund of an old deposit is not modelled. |
| `unexpected_expense` | One-off cost | `amount_chf` CHF 958 (dataset median) |
| `custom` | Adds monthly income and expense changes for a period, plus a one-off cost | All 0; `duration_months` 0 means permanent |

Retirement is not a decision. When `age` reaches `retirement_age` within the horizon, income switches to `pension_share_of_income` × the last income in every path, including the baseline.

### Calculation

Each month *t*, with `E = rent + other fixed + discretionary + irregular` and income including the monthly bonus share before retirement:

```text
C(t+1) = C(t) + I(t) − E(t) − K(t) − debt payments(t) − deposits(t)
net worth = cash + investments + deposits − debt
```

Debt accrues interest at `annual_interest_rate / 12` and is reduced by its monthly payment. Investments grow at `(1 + annual_return) ** (1/12) − 1`. The monthly low point is the cash left after one-off costs and deposits but before income.

### Outputs

`POST /api/project` takes `{"profile", "scenarios": {name: [events]}, "horizon_years", "start"}`. `horizon_years` defaults to 10 (maximum 50), `start` defaults to the current month, and there can be up to five scenarios with up to ten events each. The response contains:

- `paths`: a `Baseline` without decisions plus each named scenario. Each has a monthly `timeline` (income, expenses, one-off costs, deposits, debt flows, cash, low point, investments, debt, net worth, runway and event labels) and `metrics`: closing cash and net worth, minimum cash and its month, first negative month, months in shortfall, average net cash flow, savings rate, closing runway and goal results. Goals share cash in priority order, so a met goal reduces the cash available for later ones.
- `band`: pessimistic and optimistic cash and net-worth lines. They move dataset defaults and assumptions by ±15% in the unfavourable or favourable direction; values the user entered stay fixed.
- `comparisons`: for each scenario against the baseline, the cash and net-worth difference at the end, the largest cash gap and its month, and the break-even month after which net worth stays at or above the baseline (`null` if not reached within the horizon).
- `assumptions`: every value not entered by the user, with its source and note.

### Housing and frozen replay

For a rental change, a deposit reduces available cash and increases locked assets. A purchase with a positive purchase price consumes the down payment, fees and moving costs. In the simplified mode the engine locks the equity contribution and charges maintenance and mortgage interest as recurring costs. With `detailed_housing: 1` it books the property asset, opens the mortgage for the financed balance and amortizes it using its rate and term. Principal reduces both cash and debt. Fees reduce wealth. The engine reports the cash shortfall if the purchase requires more cash than is available.

Pass the returned `scenario_record` as the sole payload field to `POST /api/project` to reuse the resolved profile, events, timing, inflation and input provenance, including explicit nulls. This bypasses fresh event lookup and default resolution. Use this contract for generalized projections and the downloaded run verifier for an interactive node tree.

### Interface and API

The untouched five-year Alexia preset and the offline control use `GET /api/demo/alexia`, which builds a fixed work baseline and reviewed study intervention without AI. The homepage supports story extraction, review, simulation, branch proposal and application, node editing, sensitivity, path reports and run downloads. The canonical comparison returns its full bundle from `GET /api/comparisons/alexia`. For programmatic integration, `GET /api/schema` describes accepted fields and bounds, `GET /api/events/presets` lists unexpired public milestone references, and `POST /api/project` accepts up to five named decision paths. `POST /api/branch` remains a one-step compatibility API. [Worked demonstration and startup](README.md)
