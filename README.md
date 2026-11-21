# AI Financial Twin

## The problem

Financial decisions such as studying, changing careers or moving can affect cash and net worth for years. Their consequences are difficult to compare when income, expenses and one-off costs change over time. AI Financial Twin helps people explore a decision against a baseline using their own financial situation and explicit assumptions.

## Start the server

You need Python 3 and either Docker Compose or the existing local PostgreSQL cluster. From the repository root, run:

```sh
./start.sh
```

The script creates the Python environment and `.env` if needed, installs missing dependencies, starts the default PostgreSQL database and launches the server. Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Stop the server with Ctrl+C.

To configure the AI provider, set `AI_BASE_URL`, `AI_API_KEY_MODEL` and one or more keys `AI_API_KEY_1`, `AI_API_KEY_2`, … in `.env`, then restart the server. Requests take turns across the keys and skip a rate-limited one. The same story always gives the same numbers, and any run can be replayed exactly; see [technical documentation](TECHNICAL.md#same-input-same-numbers). Choose a currency on the assumptions page or the results page to see and enter amounts in it; the calculation stays in CHF at the European Central Bank rate; see [currencies](TECHNICAL.md#currencies).

Startup seeds 32 Swiss education and housing presets in PostgreSQL. Use `GET /api/events/presets` to list current presets. On `POST /api/project`, supply a short public milestone in the event’s `description` or `label`, such as `Master's at EPFL`. A cache miss can trigger a grounded provider request. The projection assumptions include the source, references and estimation notes. Set `LIFE_EVENT_CACHE_TTL_DAYS` to change the 180-day lifetime for newly inserted or refreshed records.

See [technical documentation](TECHNICAL.md) for engineering details.
