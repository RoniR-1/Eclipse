# LauzHack 2026 launch checklist

Original audit at commit `a00df29` on 2026-11-21. Launch-pass evidence was added on 2026-11-22. A checked item identifies an implemented capability and the evidence inspected. An unchecked item is missing, unpolished, or still needs verification. Priorities reflect this audit's assessment. The judging rubric and submission guide are proposed guidance, and official organizer instructions take precedence.

The original challenge brief's minimum demonstration asks for a fictional profile, one working scenario, a before/after comparison, and transparent assumptions. The proposed scorecard weights financial modelling 25%, scenario exploration 20%, technical execution 15%, communication 15%, innovation 15%, and responsible design 10%. See `ai-financial-twin-lauzhack-2026/docs/Lauzhack 2026 - AI Financial Twin Challenge - Author Stefania Stan.pdf` (page 17), `evaluation/judging-rubric.md`, `evaluation/judge-scorecard.md`, and `evaluation/demo-and-submission.md` under the challenge directory.

Also reviewed the challenge README, modelling and data guidance, evaluation overview, technical validation and responsible AI checklists, submission and troubleshooting FAQs, and the root README, technical documentation, failure guide and phase audits. Checked items below refer to the named interface or engine only. P0 means a submission or demo blocker for this team. The draft guidance does not require a hosted site, slide deck, Monte Carlo model or all 120 scenarios.

## Engine and backend

- [x] Monthly cash, assets, liabilities, net worth, interest, principal and transfers are explicitly calculated. `financial_twin/engine.py`, `financial_twin/projection.py`, and `tests/test_accounting_invariants.py` implement and exercise the identities.
- [x] Boundary cases include zero income and interest, paid-off debt, higher tuition, negative investment returns, inflation and funding shortfalls. See `tests/test_accounting_invariants.py`, `tests/test_engine.py` and `tests/test_projection.py`.
- [x] Housing, deposits, loan servicing, event effects and baseline versus alternative comparisons are represented by the projection engine. See `financial_twin/projection.py`, `financial_twin/comparison.py`, `tests/test_projection.py` and `tests/test_alexia_comparison.py`.
- [x] Canonical ten-year comparison independently rerun with the supplied starter files. Working ends with CHF 152,000, studying with CHF 144,600, and moving abroad with CHF 97,500. The master's path needs CHF 18,600 at its lowest point and first has a funding gap in the period beginning July 2025. All three bundle checks had zero residual on every path. These figures use the starter tuition of CHF 2,500 per charge. The homepage chip uses different assumptions and a five-year horizon. See `financial_twin/comparison.py` and `tests/test_alexia_comparison.py`.
- [x] Synthetic-ledger replay checks measured closing balances for 50 profiles, and monthly accounting identities are exercised on 40 seeded paths. Housing tests cover both a locked deposit transfer and property purchase with mortgage debt. See `tests/test_projection.py`, `tests/test_general_comparison.py` and `tests/test_accounting_invariants.py`.
- [x] The engine preserves negative cash and records the funding need instead of silently clamping it. The horizon summary and node inspector show funding and reserve breaches in `web/app.js`.
- [x] Currency conversion is performed in code with dated rates and offline fallbacks, while calculations are in CHF. See `financial_twin/currency.py`, `web/app.js`, `TECHNICAL.md` and `tests/test_currency.py`.
- [x] [P1 - High Rubric Impact] **Financial modelling and correctness / Responsible design and transparency.** Input review and results explain net income, CHF calculations and FX limitations. See `web/index.html`.
- [x] [P1 - High Rubric Impact] **Financial modelling and correctness / Technical execution and validation.** The exact five-year preset opens work and study paths with two tuition charges. The hand calculation, dated low point and regression agree. See `fixtures/demo/homepage-evidence.json`, `tests/test_alexia_comparison.py` and `README.md`.

## AI extraction and error handling

- [x] The homepage sends a natural-language story for structured extraction, validates the result and presents inferred values and three to five adjustable controls. `financial_twin/server.py` handles `/api/extract` and `/api/simulate`, while `web/index.html` and `web/app.js` implement the flow.
- [x] Python computes monthly results from validated inputs. Branch proposals are reviewed and applied through deterministic engine operations. See `financial_twin/server.py`, `financial_twin/branching.py` and `tests/test_branching.py`.
- [x] Missing keys, rejected keys, provider rate limits and model fallback have server-side handling and test cases. See `financial_twin/server.py`, `tests/test_api_keys.py`, `tests/test_web.py` and `FAILURE_HANDLING.md`.
- [x] Named education and housing events can draw missing figures from PostgreSQL presets or a constrained, cited lookup. The resolver validates parameter bounds and handles a failed lookup. See `financial_twin/event_resolver.py`, `tests/test_event_resolver.py`, and `tests/test_event_cache_postgres.py`.
- [x] Validated AI results are cached, and saved runs record extracted inputs for deterministic replay. See `financial_twin/server.py`, `financial_twin/reproduce.py`, `tests/test_ai_cache.py`, `tests/test_reproducibility.py` and `examples/sample-run.json`.
- [x] [P0 - Must Have] **Technical execution and validation.** Interactive exports record ordered edits and branches. Python replays legacy and current runs, including deleted branches. See `tests/test_frontend.py` and `tests/test_reproducibility.py`.
- [x] [P1 - High Rubric Impact] **Technical execution and validation.** The verifier requires one complete, unique expected row per retained node, detects duplicate IDs in either list and reports structural errors. See `financial_twin/reproduce.py` and `tests/test_reproducibility.py`.
- [x] [P1 - High Rubric Impact] **Technical execution and validation / User experience and communication.** Real Chrome verified missing credentials plus controlled provider HTTP 401 and 429 on intake and branch proposal. Both drafts survived the demo fallback. See `evaluation/verify-credentials.cjs`.
- [x] [P1 - High Rubric Impact] **Responsible design and transparency / User experience and communication.** Review labels distinguish provider estimates, fixture/default values, assumptions and edits. Detailed input lineage accompanies the starting amounts. See `web/app.js` and `tests/web/app.test.mjs`.

## Frontend experience and visualizations

- [x] Timeline and tree views support branching, visual path comparison, cash and net-worth charts, milestone details and parent-path deltas. See `web/index.html`, `web/app.js` and `tests/web/app.test.mjs`.
- [x] Goal outcomes, deadlines, reserved cash and failure reasons appear in the node inspector when goals are present. Extraction requests goals with a target and deadline. See `financial_twin/server.py` and `web/app.js`.
- [x] Optional sensitivity ranges, first pessimistic shortfall and drivers are calculated and displayed. See `financial_twin/uncertainty.py`, `web/app.js` and `tests/test_uncertainty.py`.
- [x] The horizon report exposes one-at-a-time sensitivity, model checks and model context. Node details expose funding, goals, fork commitments and input sources. See `financial_twin/engine_report.py`, `financial_twin/report.py` and `web/app.js`.
- [x] [P1 - High Rubric Impact] **Scenario and counterfactual exploration / User experience and communication.** The unchanged five-year preset opens a calculated original path and master’s alternative with the same horizon, comparative balances, minimum liquidity and dated gap. See the README rehearsal.
- [x] [P1 - High Rubric Impact] **User experience and communication / Innovation and conceptual depth.** The results compare funding feasibility and terminal balances. The proposal review summarizes editable blocks and updates the displayed amounts. See `web/app.js`.
- [x] [P1 - High Rubric Impact] **Scenario and counterfactual exploration / User experience and communication.** Homepage controls expose the ten-year starter bundle and editable generalized comparisons in addition to the interactive node tree. See `web/index.html`, `web/app.js` and `tests/web/app.test.mjs`.
- [x] [P2 - Nice to Have] **Scenario and counterfactual exploration.** Review supports editing the target, month and priority. The calculated preview uses the simulation endpoint. See `web/app.js` and the browser-logic suite.
- [x] [P1 - High Rubric Impact] **User experience and communication / Technical execution and validation.** Chrome accessibility-tree and keyboard checks covered intake, review, charts, branch proposals and error alerts. The inspector restores focus and the chart regions scroll independently on small screens. See [test evidence and scope](evaluation/verification.md).

### Historical `comparison_bundle` visibility at a00df29

The story simulation and branch endpoint return a single-path `engine_comparison_bundle` with an empty `comparisons` map. The results horizon's **Inspect sensitivity and checks** control reads `paths[baseline_path]`. The separate `POST /api/project` and `GET /api/comparisons/alexia` provide generalized and canonical comparison data, but the homepage does not call them. `financial_twin/report.py`, `financial_twin/engine_report.py`, `financial_twin/comparison.py`, and `web/app.js` establish these paths.

| Bundle or related capability | Browser visibility at the audited revision |
| --- | --- |
| `baseline_path`, paths and `comparisons` | Horizon report selects its one path. The chart and horizon KPIs compare a branch with its parent using node data. Generalized and Alexia multi-path comparison maps are not connected to the homepage. The generalized API computes the largest cash gap and its date, and the sustained net-worth break-even date in `projection.py`. Neither has a labelled result in the main comparison. |
| `scenario_record`, assumptions and per-path provenance | No complete bundle view. The node inspector separately shows available scenario provenance. The initial review shows inferred figures and a summary. |
| Path metrics, events and commitments | Horizon KPIs and node details show selected corresponding data. Fork commitments appear in the node inspector. The complete bundle records are not presented. |
| Sensitivity drivers | Horizon report shows label, source, base and perturbed values, units, terminal cash and minimum-liquidity deltas, and goal deadline-gap effect. Driver scope, index, explicit perturbation, real-cash impact and other goal impacts are omitted. |
| Sanity checks and limitations | Horizon report shows check names and pass/fail/availability plus bundle limitations. Maximum reconciliation errors are omitted. |
| Timeline and uncertainty | Timeline and chart use nodes rather than a bundle timeline. The chart switches cash/net worth and overlays parent paths and an optional range. Real values, assets and debt are not chart selectors. |

## Responsible AI and disclosures

- [x] The interface says the story is sent to AI, and provider keys are configured server-side with `/api/config` exposing only whether a key exists. See `web/index.html`, `financial_twin/server.py` and `.gitignore`.
- [x] The Python engine produces the numbers and `node_narrative` formats values from a computed node. See `financial_twin/server.py` and `financial_twin/engine.py`.
- [x] Technical documentation describes synthetic source data, uncertainty, calculation logic and limitations. See `TECHNICAL.md` and `ai-financial-twin-lauzhack-2026/Data/README.md`.
- [x] [P0 - Must Have] **Responsible design and transparency.** Intake and results identify fictional data and conditional simulations, explain the advice limitation and request synthetic inputs.
- [x] [P0 - Must Have] **Responsible design and transparency.** The intake privacy disclosure describes provider requests, server-side response files, milestone cache, downloads, retention and deletion. See `financial_twin/server.py` and `financial_twin/event_resolver.py`.
- [x] [P1 - High Rubric Impact] **Responsible design and transparency.** Review and results explain the income, inflation, funding, liquidation, FX and synthetic-sample assumptions. Sensitivity is labelled as a stress test.
- [x] [P1 - High Rubric Impact] **Responsible design and transparency / Demo and submission readiness.** The inventory records source dates, transformations and terms. The challenge README reserves redistribution rights. The submission excludes organizer files and includes independently reusable fictional fixtures. See `evaluation/attributions.md` and `fixtures/demo/README.md`.

## Testing and validation

- [x] Tests cover cash-flow and net-worth identities, debt, boundary conditions, path isolation, deterministic replay, AI cache and provider failures. See `tests/test_accounting_invariants.py`, `tests/test_branching.py`, `tests/test_reproducibility.py`, `tests/test_ai_cache.py` and `tests/test_api_keys.py`.
- [x] PostgreSQL initialization uses a transaction and advisory lock, and integration tests cover seeding and concurrency when `TEST_DATABASE_URL` is configured. See `financial_twin/database.py` and `tests/test_database.py`.
- [x] HTTP tests cover routing, malformed requests, error responses and static-file confinement. See `tests/test_http_api.py`.
- [x] Full Python suite passed, **221 tests, no skips**, with fixture provider settings and `TEST_DATABASE_URL` pointing at local PostgreSQL. The integration tests created disposable schemas. Commands were `.local/venv/bin/python -m unittest discover -s tests` and, separately, `node --test tests/web/app.test.mjs` (passed). All five original starter-kit tests passed.
- [x] Headless Chrome at 1280 × 900 opened **Test**, inspected liquidity and provenance, reviewed a fixture job-change proposal, applied it through the real branch endpoint, and opened the selected path’s sensitivity and model-check table. The smoke test reported zero page errors. Only the proposal response was stubbed in this click path.
- [x] `scripts/reproduce.py examples/sample-run.json` matched all 1,143 recorded values across 127 nodes, including from an isolated copy of tracked files with the existing Python environment. This covers the committed unedited example.
- [x] [P1 - High Rubric Impact] **Technical execution and validation / Scenario and counterfactual exploration.** The real-browser walkthrough covered live extraction and proposal, goal and node edits, an applied branch, comparisons, funding, sensitivity and export. The downloaded file matched all 1,089 recorded values. See [validation evidence](evaluation/verification.md).

## Demo and submission docs

- [x] `PORT=18091 ./start.sh` launched with the existing environment, data and dependencies. `GET /api/config` returned HTTP 200. The service was stopped after the smoke check.
- [x] `README.md` describes startup and provider configuration, while `TECHNICAL.md` describes model mechanics, limitations and replay. `examples/sample-run.json` and `scripts/reproduce.py` provide reproducibility evidence.
- [x] `web/index.html` offers Alexia, flat purchase and sabbatical story presets, and the Test button creates an AI-independent example through `/api/test`.
- [x] [P0 - Must Have] **Technical execution and validation / Demo and submission readiness.** Started an isolated submission copy with no existing venv, .env or challenge directory, against an empty PostgreSQL 16 database. Both Alexia demos, both synthetic IDs and the random demo succeeded. See `evaluation/verification.md`.
- [x] [P0 - Must Have] **Demo and submission readiness.** README walks through the working baseline, study path, monthly calculation, funding gap, sensitivities and deterministic replay without AI keys.
- [x] [P1 - High Rubric Impact] **Demo and submission readiness / Technical execution and validation.** README and TECHNICAL describe initialization, fixtures, interface and API routes, housing modes, generic defaults, cache conditions and both replay formats.
- [ ] [P0 - Must Have] **Demo and submission readiness.** Published event rules and team limits have been recorded. The exact submission URL, upload cutoff, allotted pitch time, sponsor-specific eligibility and required presentation/code format still need the participant announcement or organizer confirmation. The team’s [submission guide](evaluation/demo-and-submission.md) and [confirmation table](faq/submission-and-judging.md) identify these outstanding decisions.
