# AI Financial Twin

## The problem

Financial decisions such as studying, changing careers or moving can affect cash and net worth for years. Their consequences are difficult to compare when income, expenses and one-off costs change over time. AI Financial Twin helps people explore a decision against a baseline using their own financial situation and explicit assumptions.

## Start the server

You need Python 3 and either Docker Compose or the existing local PostgreSQL cluster. From the repository root, run:

```sh
./start.sh
```

The script creates the Python environment and `.env` if needed, installs missing dependencies, starts the default PostgreSQL database and launches the server. Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Stop the server with Ctrl+C.

To configure the AI provider, set `AI_API_KEY`, `AI_BASE_URL` and `AI_API_KEY_MODEL` in `.env`, then restart the server. You can also enter a key in the website settings for the current page session.

See [technical documentation](TECHNICAL.md) for engineering details.
