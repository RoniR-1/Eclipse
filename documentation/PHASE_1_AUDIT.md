# Phase 1 repository and data audit

All 552,295 transactions reconcile to all 28,800 monthly cash balances, with zero discrepancies to the cent. The existing Python projection engine reproduced every recorded monthly closing balance. The repository contains two simulation interfaces and a separate starter-kit reference implementation. This audit was read-only.

## Cash reconciliation and opening state

Transactions were grouped by `(profile_id, account_id, YYYY-MM)` using exact decimal arithmetic. Every group satisfied both `sum(amount_chf) == net_transactions_chf` and `opening_cash_chf + sum(amount_chf) == closing_cash_chf`. Every transaction group matched a balance row. Each of the 1,200 profiles had every month from January 2025 through December 2026, and each closing balance equaled the following month's opening balance.

| Check | Result |
|---|---:|
| Monthly net transaction discrepancies | 0 |
| Monthly closing balance discrepancies | 0 |
| Breaks between consecutive months | 0 |
| Negative monthly closes | 772 across 61 profiles |
| Lowest monthly close | CHF −78,742.77 |
| Financing-status flags inconsistent with the sign of closing cash | 0 |

Opening cash must enter net worth exactly once. For all 1,200 profiles, the profile opening cash, account opening balance, cash asset snapshot, and January opening ledger balance agree. The profile, account, and asset snapshot dates also agree. The [synthetic-profile loader](financial_twin/loaders.py) takes opening cash from the profile and skips the `cash` asset row. The other 1,199 asset rows are investment portfolios. Their account identifiers are absent from the cash-account file, so an inner join to that file would discard every investment.

## Data inventory

The challenge contains 10 logical CSVs under `Data/` and an eleventh under `docs/`. The extracted transaction CSV and the corresponding ZIP member are byte-identical. The two 120-row scenario catalogs are also byte-identical. The [data dictionary](ai-financial-twin-lauzhack-2026/Data/data-dictionary.md) describes the datasets at a high level. The field names below come from the actual headers.

| Source | Rows | Columns in file order |
|---|---:|---|
| [synthetic-profiles.csv](ai-financial-twin-lauzhack-2026/Data/synthetic-profiles.csv) | 1,200 | `profile_id`, `display_name`, `age_at_start`, `city`, `canton`, `country`, `currency`, `employment_status`, `household_type`, `opening_cash_chf`, `reference_monthly_net_income_chf`, `reference_monthly_rent_chf`, `as_of_date`, `synthetic` |
| [synthetic-accounts.csv](ai-financial-twin-lauzhack-2026/Data/synthetic-accounts.csv) | 1,200 | `account_id`, `profile_id`, `account_type`, `currency`, `opening_balance_chf`, `as_of_date` |
| [synthetic-transactions.csv](ai-financial-twin-lauzhack-2026/Data/synthetic-transactions/synthetic-transactions.csv) | 552,295 | `transaction_id`, `profile_id`, `date`, `transaction_type`, `category`, `amount_chf`, `currency`, `account_id`, `description`, `synthetic` |
| [synthetic-cash-balances.csv](ai-financial-twin-lauzhack-2026/Data/synthetic-cash-balances.csv) | 28,800 | `profile_id`, `account_id`, `month`, `opening_cash_chf`, `net_transactions_chf`, `closing_cash_chf`, `currency`, `status` |
| [synthetic-assets.csv](ai-financial-twin-lauzhack-2026/Data/synthetic-assets.csv) | 2,399 | `asset_id`, `profile_id`, `as_of_date`, `asset_type`, `value_chf`, `currency`, `account_id`, `valuation_basis` |
| [synthetic-liabilities.csv](ai-financial-twin-lauzhack-2026/Data/synthetic-liabilities.csv) | 283 | `liability_id`, `profile_id`, `as_of_date`, `liability_type`, `opening_principal_chf`, `annual_interest_rate`, `monthly_payment_chf`, `currency`, `notes` |
| [synthetic-goals.csv](ai-financial-twin-lauzhack-2026/Data/synthetic-goals.csv) | 1,500 | `goal_id`, `profile_id`, `goal_name`, `target_amount_chf`, `target_date`, `priority`, `currency` |
| [life-events.csv](ai-financial-twin-lauzhack-2026/Data/life-events.csv) | 600 | `event_id`, `profile_id`, `event_date`, `event_type`, `description`, `status`, `cash_effect_chf`, `linked_scenario_id` |
| [scenario-assumptions.csv](ai-financial-twin-lauzhack-2026/Data/scenario-assumptions.csv) | 240 | `assumption_id`, `scenario_id`, `profile_id`, `parameter`, `value`, `unit`, `frequency`, `source_type`, `notes` |
| [scenario-catalog.csv](ai-financial-twin-lauzhack-2026/Data/scenario-catalog.csv) | 120 | `id`, `category`, `scenario`, `baseline`, `intervention`, `example_metrics` |
| [scenario-library-120.csv](ai-financial-twin-lauzhack-2026/docs/scenario-library-120.csv) | 120 | `id`, `category`, `scenario`, `baseline`, `intervention`, `example_metrics` |

The CSVs encode identifiers and categories as text, age as an integer, monetary amounts as decimal CHF, annual interest rates as decimal fractions, dates as `YYYY-MM-DD`, ledger months as `YYYY-MM`, and synthetic flags as lowercase boolean text. An assumption's `value` takes its meaning from `parameter`, `unit`, and `frequency`. All populated currency fields were `CHF`. The audit found zero duplicate record keys and zero orphan profile references.

Three details affect simulation behavior directly.

- All 522,603 expense entries carry a negative sign. All 29,692 income entries carry a positive sign. Summing signed amounts reproduces the ledger.
- All 283 liabilities are opening personal-loan snapshots with a CHF 0 scheduled monthly payment. The file states that they are not amortized in the transaction ledger. Any future repayment schedule is a projection assumption.
- All 600 life events have status `proposed_not_executed` and a blank `cash_effect_chf`. Of those, 473 also have a blank `linked_scenario_id`. These were the only blank fields found across the CSVs. Applying an event during historical replay would change the supplied history.

## Alexia and the master's assumptions

The documentation references `Data/alexia-profile.json`, but that file is absent from this checkout. Alexia appears as CSV profile `P00001` and in the starter kit's [sample profile](ai-financial-twin-lauzhack-2026/starter-kit/sample_profile.json). She starts at age 20 in Lausanne with CHF 8,000. The starter JSON sets a 120-month horizon and defines three paths, with **Start working** as the work-now comparison. The paths are defined in [scenario_config.json](ai-financial-twin-lauzhack-2026/starter-kit/scenario_config.json).

| Starter path | Monthly income / expenses in CHF | One-off costs in CHF, zero-based months | Closing cash, CHF | Minimum cash including opening, CHF |
|---|---|---|---:|---:|
| Start working | 4,200 / 3,000 for 120 months | None | 152,000 | 8,000 |
| Master's degree | 1,200 / 2,100 for 24 months, then 4,800 / 3,100 for 96 months | 2,500 at month 0 and 2,500 at month 12 | 144,600 | −18,600 |
| Move abroad | 3,700 / 2,900 for 120 months | 6,500 at month 0 | 97,500 | 2,300 |

The master's path first closes below zero in the seventh month. Its end-of-study balance is `8,000 + 24 × (1,200 − 2,100) − 2 × 2,500 = −18,600 CHF`. The following 96 months add CHF 163,200. A comparison based only on the ten-year closing balance would hide the interim financing need.

S001 has three distinct sources of tuition assumptions. The CSV supplies two parameters each for 120 profiles. Every duration is two years, giving a median of 24 months. Annual tuition takes values of CHF 1,200, 2,500, 4,800, and 8,000, with a median of CHF 4,800 per year. Alexia's own S001 row specifies CHF 8,000 per year. The starter path separately specifies two CHF 2,500 charges. Reproducing a result requires recording which source supplied the tuition.

## Repository structure and interfaces

| Location | Role and verified interface |
|---|---|
| `ai-financial-twin-lauzhack-2026/` | Challenge checkout containing `Data/`, `docs/` including the PDF brief, `evaluation/`, `examples/`, `faq/`, `starter-kit/`, and resource directories. Both `resources/` and a directory named `resources ` with a trailing space exist. |
| `ai-financial-twin-lauzhack-2026/starter-kit/` | Standalone cash-only reference. `simulate(profile, scenario)` returns monthly cash, closing cash, minimum cash, and the first negative month. Phase intervals are zero-based and end-exclusive. [Reference implementation](ai-financial-twin-lauzhack-2026/starter-kit/financial_twin.py) |
| `financial_twin/engine.py` | Branchable monthly simulation. `create_root(scenario)` establishes opening state. `simulate(node, months, ...)` returns monthly child nodes with cash, assets, debts, net worth, flows, and parent identifiers. [Engine](financial_twin/engine.py) |
| `financial_twin/projection.py` | `project(profile, scenarios=None, *, horizon_years=10, start=None, bands=True)` compares named event paths with a generated `Baseline`. `schema()` describes inputs. Outputs include timelines, metrics, assumptions, warnings, and optional sensitivity bands. [Projection interface](financial_twin/projection.py) |
| `financial_twin/loaders.py` | `load_synthetic_profile(...)` requires an explicit monthly expense amount. `load_starter_scenario(...)` requires the two JSON paths, scenario name, and an explicit start date. [Loaders](financial_twin/loaders.py) |
| `financial_twin/server.py`, `scripts/`, `tests/`, `web/` | HTTP routes include `GET /api/schema`, `POST /api/project`, `POST /api/simulate`, and `POST /api/branch`. The remaining directories hold data and defaults tooling, regression tests, and the frontend. [Server](financial_twin/server.py) · [Technical documentation](TECHNICAL.md) |

## Validation and limitations

Explorers inspected the data, challenge materials, and backend contracts. A worker ran 52 passing offline tests and verified all 360 starter-path monthly balances through the backend loader and engine. The checked-in projection replay test covers 50 profiles at one decimal place. A separate full-data replay for this audit aggregated inputs in integer cents and checked `projection.run_arrays` against all 1,200 profiles at cent precision. PostgreSQL integration and live provider calls were outside this phase. [Loader tests](tests/test_loaders.py) · [Projection tests](tests/test_projection.py)
