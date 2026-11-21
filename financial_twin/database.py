import csv
import os
from pathlib import Path

import psycopg
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1] / "ai-financial-twin-lauzhack-2026"
TABLES = (
    ("profiles", "Data/synthetic-profiles.csv", "profile_id text PRIMARY KEY, display_name text, age_at_start integer, city text, canton text, country text, currency text, employment_status text, household_type text, opening_cash_chf numeric, reference_monthly_net_income_chf numeric, reference_monthly_rent_chf numeric, as_of_date date, synthetic boolean"),
    ("scenario_catalog", "Data/scenario-catalog.csv", "id text PRIMARY KEY, category text, scenario text, baseline text, intervention text, example_metrics text"),
    ("scenario_library", "docs/scenario-library-120.csv", "id text PRIMARY KEY, category text, scenario text, baseline text, intervention text, example_metrics text"),
    ("accounts", "Data/synthetic-accounts.csv", "account_id text PRIMARY KEY, profile_id text REFERENCES profiles(profile_id), account_type text, currency text, opening_balance_chf numeric, as_of_date date"),
    ("assets", "Data/synthetic-assets.csv", "asset_id text PRIMARY KEY, profile_id text REFERENCES profiles(profile_id), as_of_date date, asset_type text, value_chf numeric, currency text, account_id text, valuation_basis text"),
    ("liabilities", "Data/synthetic-liabilities.csv", "liability_id text PRIMARY KEY, profile_id text REFERENCES profiles(profile_id), as_of_date date, liability_type text, opening_principal_chf numeric, annual_interest_rate numeric, monthly_payment_chf numeric, currency text, notes text"),
    ("goals", "Data/synthetic-goals.csv", "goal_id text PRIMARY KEY, profile_id text REFERENCES profiles(profile_id), goal_name text, target_amount_chf numeric, target_date date, priority text, currency text"),
    ("cash_balances", "Data/synthetic-cash-balances.csv", "profile_id text REFERENCES profiles(profile_id), account_id text REFERENCES accounts(account_id), month text, opening_cash_chf numeric, net_transactions_chf numeric, closing_cash_chf numeric, currency text, status text, PRIMARY KEY (account_id, month)"),
    ("scenario_assumptions", "Data/scenario-assumptions.csv", "assumption_id text PRIMARY KEY, scenario_id text REFERENCES scenario_catalog(id), profile_id text REFERENCES profiles(profile_id), parameter text, value text, unit text, frequency text, source_type text, notes text"),
    ("life_events", "Data/life-events.csv", "event_id text PRIMARY KEY, profile_id text REFERENCES profiles(profile_id), event_date date, event_type text, description text, status text, cash_effect_chf numeric, linked_scenario_id text REFERENCES scenario_catalog(id)"),
    ("transactions", "Data/synthetic-transactions/synthetic-transactions.csv", "transaction_id text PRIMARY KEY, profile_id text REFERENCES profiles(profile_id), date date, transaction_type text, category text, amount_chf numeric, currency text, account_id text REFERENCES accounts(account_id), description text, synthetic boolean"),
)


def initialize_database(database_url, data_root=ROOT):
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(739628105)")
            for name, _, definition in TABLES:
                cursor.execute(sql.SQL("CREATE TABLE IF NOT EXISTS {} ({})").format(sql.Identifier(name), sql.SQL(definition)))
            for table in ("accounts", "assets", "liabilities", "goals", "cash_balances", "scenario_assumptions", "life_events", "transactions"):
                cursor.execute(sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (profile_id)").format(sql.Identifier(f"{table}_profile_id_idx"), sql.Identifier(table)))
            for name, _, _ in TABLES:
                cursor.execute(sql.SQL("SELECT EXISTS (SELECT 1 FROM {} LIMIT 1)").format(sql.Identifier(name)))
                if cursor.fetchone()[0]:
                    return False
            for name, relative_path, _ in TABLES:
                path = Path(data_root) / relative_path
                cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = %s ORDER BY ordinal_position", (name,))
                columns = [row[0] for row in cursor.fetchall()]
                with path.open(encoding="utf-8-sig", newline="") as source:
                    if next(csv.reader(source), None) != columns:
                        raise ValueError(f"CSV header does not match {name}: {path}")
                with path.open("rb") as source:
                    with cursor.copy(sql.SQL("COPY {} FROM STDIN WITH (FORMAT CSV, HEADER TRUE)").format(sql.Identifier(name))) as copy:
                        while chunk := source.read(1024 * 1024):
                            copy.write(chunk)
    return True


def initialize_from_environment():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL must be set to a PostgreSQL connection URL")
    return initialize_database(database_url)
