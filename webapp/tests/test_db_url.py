"""Tests for database URL normalization (Railway/managed Postgres compatibility)."""

import os
import sys
import unittest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from webapp.db.session import normalize_db_url  # noqa: E402


class NormalizeDbUrlTest(unittest.TestCase):

    def test_railway_postgresql_gets_psycopg_driver(self):
        self.assertEqual(
            normalize_db_url("postgresql://u:p@host:5432/db"),
            "postgresql+psycopg://u:p@host:5432/db",
        )

    def test_legacy_postgres_scheme_is_upgraded(self):
        # Some providers still emit the old postgres:// scheme.
        self.assertEqual(
            normalize_db_url("postgres://u:p@host:5432/db"),
            "postgresql+psycopg://u:p@host:5432/db",
        )

    def test_already_qualified_url_is_untouched(self):
        url = "postgresql+psycopg://u:p@host/db"
        self.assertEqual(normalize_db_url(url), url)

    def test_sqlite_url_is_untouched(self):
        url = "sqlite:///shiftwork_web.db"
        self.assertEqual(normalize_db_url(url), url)


if __name__ == "__main__":
    unittest.main(verbosity=2)
