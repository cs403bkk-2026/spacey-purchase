import os

import psycopg
import pytest
from psycopg.rows import dict_row

import purchase.schema

LOCAL_DATABASE_URL = "postgresql://purchase:purchase@localhost:5435/purchase"


@pytest.fixture
def cur():
    """A cursor on the Purchase tables; everything it changes is rolled back."""
    url = os.getenv("DATABASE_URL", LOCAL_DATABASE_URL)
    with psycopg.connect(url, row_factory=dict_row) as conn, conn.cursor() as cursor:
        purchase.schema.create_tables(cursor)
        yield cursor
        conn.rollback()
