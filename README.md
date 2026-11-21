# AI Financial Twin

## Run the website

From the repository root, run `./start.sh` and open [the local website](http://127.0.0.1:8000). The shortcut creates the Python environment and `.env` if needed, installs missing dependencies, and starts the default PostgreSQL database using the existing local cluster or Docker Compose. Python 3 and either Docker Compose or the existing local PostgreSQL cluster are required. Stop the website with Ctrl+C.

To configure the AI provider, copy `.env.example` to `.env`, set `AI_API_KEY`, `AI_BASE_URL`, and `AI_API_KEY_MODEL`, and restart the server. Put model names in preference order on one line, separated by commas, such as `AI_API_KEY_MODEL=model1,model2,model3`. When a model is unavailable, rate-limited, or times out, the server tries the next model and waits 60 seconds before trying the failed model on a later request. Set `AI_MODEL_RETRY_SECONDS` to change that interval. If all models are cooling down, the server tries them in order. You can also enter a key through the website’s settings for the current page session.

Describe your finances and the decision you want to explore, using CHF for monetary amounts. Select a 5-, 10- or 20-year horizon and submit. The server sends the statement to Google Gemini to extract a starting scenario and choose three to five relevant controls. Review the assumptions, adjust the sliders, and choose **Simulate Future** to project the monthly timeline with the Python engine.

## PostgreSQL data

The server requires `DATABASE_URL`. The provided Docker Compose database listens on localhost port 5433 and stores data in the `financial_twin_data` named volume, which survives application and database restarts. A local PostgreSQL instance or hosted PostgreSQL database can also be used by setting its URL in `.env`. Start the database before starting the server. The existing local PostgreSQL cluster in this workspace can instead be started with `/usr/lib/postgresql/16/bin/pg_ctl -D .local/postgres -l .local/postgres.log -o '-p 5433 -k /tmp' start` and stopped with `/usr/lib/postgresql/16/bin/pg_ctl -D .local/postgres stop`. Use either this local cluster or Compose at a time, since both use port 5433.

On startup, PostgreSQL tables are created if needed. If every application data table is empty, all eleven supplied CSV files are loaded in one transaction under an advisory lock. If any table contains data, the import is skipped; subsequent starts do not read the CSVs. A failed import rolls back, and concurrent startups cannot duplicate the seed. To deliberately reset data, empty all application tables or create a new database; removing the Compose volume permanently deletes the stored data. The scenario catalog and the identical scenario library are retained as separate source tables. Most records link to profiles, accounts, or scenarios through foreign keys; asset account references remain unconstrained because some investment accounts are absent from the accounts CSV.

To run the PostgreSQL integration test against a disposable schema, set `TEST_DATABASE_URL` to a database where the user can create schemas, then run `.local/venv/bin/python -m unittest discover -s tests -v`.

## Simulation engine

This Python package models monthly financial decisions for the LauzHack challenge. Its inputs and checkpoint nodes are JSON-compatible so a web backend can persist them and a browser can render paths without reproducing financial calculations. It uses NumPy for asset and debt calculations.

## Challenge materials

Stefania Stan’s [challenge brief](ai-financial-twin-lauzhack-2026/README.md) asks teams to let a user change a life decision and compare its consequences against a baseline. The examples follow Alexia, a fictional student in Lausanne with CHF 8,000 in cash. The [evaluation materials](ai-financial-twin-lauzhack-2026/evaluation/README.md) give draft judging guidance. They emphasize a demonstrable decision, sound accounting, visible assumptions, constraint breaches and explainable comparisons. Binding event rules come from the organizers.

The [Data directory](ai-financial-twin-lauzhack-2026/Data) contains 1,200 synthetic profiles, 1,200 accounts, 2,399 asset snapshots, 283 liability snapshots, 1,500 goals and 28,800 monthly cash reconciliations. The 120 catalog entries describe decisions. The 240 scenario assumption rows cover only a subset of profile/scenario combinations. The transaction and life-event CSVs are also loaded. Net monthly transactions alone cannot identify the underlying income and expense categories.

The [starter kit](ai-financial-twin-lauzhack-2026/starter-kit) supplies an independent cash-only reference with three ten-year paths. Its JSON assumptions differ from the CSV scenario assumptions. For example, the JSON charges Alexia CHF 2,500 at the beginning of each study year, whereas her S001 CSV assumption specifies CHF 8,000 per year. Select one set explicitly when comparing paths.

## Modelling choice

Use a deterministic monthly balance sheet and branch at decision dates. It fits the frequency of the supplied cash balances, makes individual cash movements auditable and lets a user hold everything else fixed when changing one decision. A portfolio allocation, return assumption or tuition shock can be varied in a separate path. The supplied snapshots do not calibrate market-return distributions, correlations or personal risk preferences, so attaching probabilities to these paths would require additional evidence.

Keep net pay, recurring expenses and one-off costs separate. Debt payments reduce cash, interest accrues on outstanding debt, and principal repayment reduces the liability. Transfers from cash into investments change liquidity while preserving net worth at the instant of transfer. Each monthly checkpoint therefore needs both a balance sheet and a record of that month's flows. A negative cash balance measures the funding gap under the chosen assumptions. A user can inspect that gap even when the ending net worth is positive.

## Run and branch

The model applies these rules once per month, in this order.

| Quantity | Calculation and interpretation |
| --- | --- |
| Living costs | Base monthly expenses × opening price index. The index starts at 1 and grows by `(1 + annual_inflation) ** (1/12)` per month. Income and dated one-offs use explicit nominal CHF assumptions. |
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

Selecting a milestone opens **What if something changed?**. A user either picks a preset (no AI) or describes an event in their own words. The event is first turned into editable building blocks, shown in a review card, and only simulated after the user confirms.

| Block | Meaning |
| --- | --- |
| Monthly change | Income or expense change per month, with a start offset and a duration (0 = permanent), for example −3,000 income for 24 months, then +1,000 from month 24 |
| One-off amount | A single amount at an offset: positive for a cost, negative for money received (booked as one month of extra income) |
| Purchase | An asset bought at the branch point with an optional loan. The server enforces price = loan + down payment, so a purchase never creates or destroys wealth; only the purchase costs reduce net worth. Annual value change models appreciation or depreciation. A rent deposit is a purchase of kind `deposit`. |
| Investment change | Revalues existing investments at the branch point (0.7 = −30%) |

Every block carries a source (`user`, `dataset`, `assumption` or `ai_estimate`), and editing a value marks it `user`. The server returns warnings when the down payment was corrected, a loan payment is below its interest, or upfront costs exceed the available cash.

The presets in `financial_twin/branching.py` are Master's degree, Career change, Relocate, New home, Unexpected expense, Have a child, Buy property, Market shock and Go part-time. Tuition, study length, the unexpected-expense amount and the median rent come from `defaults.json`; the other values are labelled assumptions.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/branch/presets` | Preset ids and labels |
| `POST /api/branch/propose` | `{parent, preset}` or `{parent, event}` → `{proposal, origin}`. Only the text variant calls the AI. |
| `POST /api/branch/apply` | `{parent, event, months, variables, proposal}` → branch nodes. Deterministic; the first node stores the applied proposal as `branch_proposal`. |
| `POST /api/branch` | One step (propose and apply without review), kept for compatibility |

`/api/branch/apply` splits the engine's phases at every block boundary, so temporary changes end on time and their start and end appear as milestones. The random test scenario builds its branches from the presets. `tests/test_branching.py` checks durations, purchase accounting, money received, warnings, every preset and the AI and no-AI paths.

## Check the implementation

Run `python3 -m unittest discover -s tests -v` from the workspace root with the challenge checkout present. The starter's original tests can also be run from `ai-financial-twin-lauzhack-2026/starter-kit` with `python3 -m unittest -v`.
