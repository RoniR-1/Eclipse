import csv
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest
import uuid
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from financial_twin.database import ROOT, TABLES, initialize_database


class FixtureTests(unittest.TestCase):
    def test_ledger_balances_and_unique_assumptions(self):
        from collections import defaultdict
        from decimal import Decimal

        def rows(path):
            with (ROOT / path).open(encoding="utf-8", newline="") as source:
                return list(csv.DictReader(source))

        transactions = defaultdict(Decimal)
        for item in rows("Data/synthetic-transactions/synthetic-transactions.csv"):
            transactions[(item["profile_id"], item["account_id"], item["date"][:7])] += Decimal(item["amount_chf"])
        previous = {}
        for item in sorted(rows("Data/synthetic-cash-balances.csv"), key=lambda row: (row["account_id"], row["month"])):
            key = (item["profile_id"], item["account_id"], item["month"])
            opening = Decimal(item["opening_cash_chf"])
            net = Decimal(item["net_transactions_chf"])
            closing = Decimal(item["closing_cash_chf"])
            self.assertEqual(transactions.pop(key), net)
            self.assertEqual(opening + net, closing)
            if item["account_id"] in previous:
                self.assertEqual(opening, previous[item["account_id"]])
            previous[item["account_id"]] = closing
        self.assertFalse(transactions)
        assumptions = rows("Data/scenario-assumptions.csv")
        keys = [(item["profile_id"], item["scenario_id"], item["parameter"]) for item in assumptions]
        self.assertEqual(len(keys), len(set(keys)))


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "set TEST_DATABASE_URL to run PostgreSQL integration tests")
class DatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True)
        cls.schema = "test_" + uuid.uuid4().hex
        cls.admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(cls.schema)))
        cls.dsn = make_conninfo(os.environ["TEST_DATABASE_URL"], options=f"-c search_path={cls.schema}")

    @classmethod
    def tearDownClass(cls):
        cls.admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(cls.schema)))
        cls.admin.close()

    def test_seed_rollback_and_skip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / TABLES[0][1]
            first.parent.mkdir(parents=True)
            first.write_text("incorrect,header\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "CSV header"):
                initialize_database(self.dsn, root)
            with psycopg.connect(self.dsn) as connection:
                self.assertIsNone(connection.execute("SELECT to_regclass('profiles')").fetchone()[0])
            shutil.copyfile(ROOT / TABLES[0][1], first)
            with self.assertRaises(FileNotFoundError):
                initialize_database(self.dsn, root)
            with psycopg.connect(self.dsn) as connection:
                self.assertIsNone(connection.execute("SELECT to_regclass('profiles')").fetchone()[0])
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _: initialize_database(self.dsn, ROOT), range(2)))
            self.assertEqual(sorted(results), [False, True])
            with psycopg.connect(self.dsn) as connection:
                for name, relative_path, _ in TABLES:
                    with (ROOT / relative_path).open(encoding="utf-8-sig", newline="") as source:
                        expected = sum(1 for _ in csv.reader(source)) - 1
                    count = connection.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(name))).fetchone()[0]
                    self.assertEqual(count, expected, name)
                initial_events = connection.execute("SELECT count(*) FROM life_event_knowledge").fetchone()[0]
                self.assertGreaterEqual(initial_events, 22)
            self.assertFalse(initialize_database(self.dsn, root))
            with psycopg.connect(self.dsn) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM life_event_knowledge").fetchone()[0], initial_events)
                connection.execute("CREATE TEMP TABLE saved_profile AS SELECT * FROM profiles LIMIT 1")
                names = sql.SQL(", ").join(sql.Identifier(name) for name, _, _ in TABLES)
                connection.execute(sql.SQL("TRUNCATE {} CASCADE").format(names))
                connection.execute("INSERT INTO profiles SELECT * FROM saved_profile")
            self.assertFalse(initialize_database(self.dsn, root))
            with psycopg.connect(self.dsn) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM profiles").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT count(*) FROM transactions").fetchone()[0], 0)
