import os

import psycopg
import pytest
from psycopg.rows import dict_row

from purchase.migrations import run_migrations

LOCAL_DATABASE_URL = "postgresql://purchase:purchase@localhost:5435/purchase"


@pytest.fixture(scope="session")
def database_url():
    """The test database, migrated once per session."""
    url = os.getenv("DATABASE_URL", LOCAL_DATABASE_URL)
    with psycopg.connect(url, autocommit=True, row_factory=dict_row) as conn:
        run_migrations(conn)
    return url


@pytest.fixture
def cur(database_url):
    """A cursor on the Purchase tables; everything it changes is rolled back."""
    with (
        psycopg.connect(database_url, row_factory=dict_row) as conn,
        conn.cursor() as cursor,
    ):
        yield cursor
        conn.rollback()
