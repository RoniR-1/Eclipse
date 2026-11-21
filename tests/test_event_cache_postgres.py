import os
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from financial_twin.database import initialize_database
from financial_twin.event_resolver import resolve_event


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "requires PostgreSQL")
class EventCacheIntegrationTests(unittest.TestCase):
    def test_persist_and_refresh(self):
        schema = "test_" + uuid4().hex
        admin = psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True)
        self.addCleanup(admin.close)
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        self.addCleanup(admin.execute, sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        dsn = make_conninfo(os.environ["TEST_DATABASE_URL"], options=f"-c search_path={schema}")
        initialize_database(dsn)
        response = {"params": {"duration_months": 18}, "source_urls": ["https://example.edu/fees"], "note": "18-month programme"}
        with patch.dict(os.environ, {"DATABASE_URL": dsn}), patch("financial_twin.event_resolver.web_lookup", return_value=response) as search:
            for kind, title in (("education", "Bachelor's at ETH Zurich"), ("education", "Master's at EPFL"), ("housing_change", "Buying an apartment in Lausanne")):
                self.assertEqual(resolve_event(kind, title, "test")["source"], "database_cache", title)
            search.assert_not_called()
            title = "Master at Example University"
            self.assertEqual(resolve_event("education", title, "test")["source"], "web_search")
            self.assertEqual(resolve_event("education", title, "test")["note"], response["note"])
            search.assert_called_once()
            with psycopg.connect(dsn) as connection:
                connection.execute("UPDATE life_event_knowledge SET expires_at = now() WHERE display_name = %s", (title,))
            self.assertEqual(resolve_event("education", title, "test")["source"], "web_search")
            self.assertEqual(search.call_count, 2)
