# AI Financial Twin

Compare working now with going back to school, or explore a fictional decision of your own. Follow the monthly cash balance, see when money runs short, and compare paths on the same date. These are conditional simulations. Use fictional inputs and keep confidential financial information out of the tool.

## Start from a clean checkout

Use Python 3.10 or later with venv and pip, Bash, and PostgreSQL. Docker with the Compose plugin can provide the database. The first installation needs access to Python packages and, if using Compose, the PostgreSQL image.

1. From the repository root, run `./start.sh`. It creates `.local/venv`, installs NumPy and Psycopg when missing, and copies `.env.example` to `.env`. With the default connection it can start the Compose database on port 5433.
2. If using your own PostgreSQL server, create an empty database owned by the application role, copy `.env.example` to `.env`, set `DATABASE_URL` to that database, then run `./start.sh`. The role needs permission to create tables and indexes.
3. Open [the homepage](http://127.0.0.1:8000). The fictional demos run without provider credentials. Ctrl+C stops the app. For Compose, `docker compose stop db` stops PostgreSQL and preserves the volume.

The included `fixtures/demo` directory supplies the comparison inputs, synthetic profile snapshots and seed files, including the extracted transaction CSV. Startup imports the seed into an empty database in one transaction and initializes public education and housing presets. No challenge download or archive extraction is required. To rehearse against a fresh database, point `DATABASE_URL` at a new empty database. [Fixture files](fixtures/demo) · [Sources and terms](evaluation/attributions.md)

For free-text story extraction and branch proposals, configure a provider key in `AI_API_KEY_1` and restart. For a chat-completions-compatible service, also set `AI_BASE_URL` and a comma-separated `AI_API_KEY_MODEL` list. Native Gemini uses `GEMINI_MODEL` when the base URL is empty. Validated assumptions and reviewed edits are the reproducible inputs. A fresh AI extraction can change the result. [Provider setup and replay](TECHNICAL.md) · [Failure handling](FAILURE_HANDLING.md)

## Deploy on Railway

1. Create a Railway project from this repository and add a PostgreSQL service.
2. In the application service variables, set `DATABASE_URL` to `${{Postgres.DATABASE_URL}}`, replacing `Postgres` if the database service has a different name. The database role needs permission to create tables and indexes.
3. Deploy the application and generate a public domain in its networking settings. Railway installs `requirements.txt`, starts the application using `railway.toml`, and supplies `PORT`. Startup creates the schema and imports the included fictional seed data into an empty database.
4. To enable AI features, set `AI_API_KEY_1` and any provider settings described above as application service variables. The application runs the fictional demos without an API key.

The AI response cache and exchange-rate cache use the application filesystem. They can be recreated after a deployment. To retain the AI response cache, attach a Railway volume and set `AI_CACHE_DIR` to a directory within its mount path. PostgreSQL data persists in the database service.

## Rehearse the five-year Alexia demo

1. Choose **Explore fictional demo without AI**. Start on 1 January 2026 with CHF 8,000. Read both paths at **1 January 2031**, after 60 monthly transitions. The working path earns CHF 4,800 net and spends CHF 2,400 per month.
2. Compare the master’s alternative. Tuition of CHF 8,000 is charged at the beginning of each study year, at months 0 and 12. Income is CHF 1,500 per month for 24 months and CHF 6,500 for the remaining 36. Living costs stay at CHF 2,400. Assume zero inflation, investment contributions, interest, and starting debt.
3. Show the first study month. Tuition uses all CHF 8,000 of opening cash. Income less living costs leaves **CHF −900** on 1 February 2026. At graduation on 1 January 2028, working has CHF 65,600 and studying has **CHF −29,600**. This is both the largest separation (CHF 95,200) and the study path’s maximum funding need.
4. At the common horizon, working has **CHF 152,000** and studying has **CHF 118,000** in both cash and net worth. The graduate saves CHF 4,100 per month, CHF 1,700 more than the worker, but the study path still trails by CHF 34,000 at year five. There is no sustained break-even during these five years. The negative balance identifies money Alexia would need to secure. Financing costs and asset sales are outside this example.
5. Validate aloud. Working gives `8,000 + 60 × (4,800 − 2,400) = 152,000`. Studying gives `8,000 − 2 × 8,000 + 24 × (1,500 − 2,400) + 36 × (6,500 − 2,400) = 118,000`. Open a path’s sensitivity report and identify the assumption being varied. A stress range describes conditional outcomes under different inputs.
6. Download the run from results. Run the command below with the downloaded file path. The verifier rebuilds the node tree, including edits and reviewed branches, and checks the retained rows. For an immediate command-line rehearsal, use the included example.

```sh
.local/venv/bin/python scripts/reproduce.py examples/sample-run.json
```

The five-year inputs, tuition months, expected funding gap and terminal balances are also saved in [the machine-readable demo evidence](fixtures/demo/homepage-evidence.json).

On the homepage, open **Explore the ten-year starter comparison** and select **Load starter comparison**. This reference uses different starting salaries, living costs and CHF 2,500 tuition charges. Its terminal cash balances are CHF 152,000 working, CHF 144,600 studying and CHF 97,500 moving abroad. The study path needs CHF 18,600 at its lowest point. Open **Run a structured comparison** to change starting cash, net pay, rent and the decision month. Choose **Compare structured scenarios** to recalculate study and relocation against the baseline.

## Verify the submission

```sh
.local/venv/bin/python -m unittest discover -s tests
node --test tests/web/app.test.mjs
```

For database integration checks, set `TEST_DATABASE_URL` to a disposable PostgreSQL database where the role can create schemas. [Browser walkthrough and validation evidence](evaluation/verification.md) · [Scenario and API contracts](TECHNICAL.md) · [Submission checklist](evaluation/demo-and-submission.md) · [Organizer rules](faq/submission-and-judging.md)
