# AI Financial Twin

## Run the website

From the repository root, run `python3 -m financial_twin.server` and open [the local website](http://127.0.0.1:8000). The website and simulation engine use the Python standard library.

To configure the AI provider, copy `.env.example` to `.env`, set `AI_API_KEY`, `AI_BASE_URL`, and `AI_API_KEY_MODEL`, and restart the server. Provide model names separated by commas to try another model when one is unavailable, rate-limited, or times out. You can also enter a key through the website’s settings for the current page session.

Describe your finances and the decision you want to explore, using CHF for monetary amounts. Select a 5-, 10- or 20-year horizon and submit. The server sends the statement to Google Gemini to extract a starting scenario and choose three to five relevant controls. Review the assumptions, adjust the sliders, and choose **Simulate Future** to project the monthly timeline with the Python engine.

## Simulation engine

This Python package models monthly financial decisions for the LauzHack challenge. Its inputs and checkpoint nodes are JSON-compatible so a web backend can persist them and a browser can render paths without reproducing financial calculations. It uses the Python standard library, matching the challenge starter.

## Challenge materials

Stefania Stan’s [challenge brief](ai-financial-twin-lauzhack-2026/README.md) asks teams to let a user change a life decision and compare its consequences against a baseline. The examples follow Alexia, a fictional student in Lausanne with CHF 8,000 in cash. The [evaluation materials](ai-financial-twin-lauzhack-2026/evaluation/README.md) give draft judging guidance. They emphasize a demonstrable decision, sound accounting, visible assumptions, constraint breaches and explainable comparisons. Binding event rules come from the organizers.

The [Data directory](ai-financial-twin-lauzhack-2026/Data) contains 1,200 synthetic profiles, 1,200 accounts, 2,399 asset snapshots, 283 liability snapshots, 1,500 goals and 28,800 monthly cash reconciliations. The 120 catalog entries describe decisions. The 240 scenario assumption rows cover only a subset of profile/scenario combinations. The README mentions a detailed transaction CSV and a life-event CSV that are absent from this checkout. Net monthly transactions alone cannot identify the underlying income and expense categories.

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

## Check the implementation

Run `python3 -m unittest discover -s tests -v` from the workspace root with the challenge checkout present. The starter's original tests can also be run from `ai-financial-twin-lauzhack-2026/starter-kit` with `python3 -m unittest -v`.
