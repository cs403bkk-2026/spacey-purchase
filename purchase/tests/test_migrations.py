import re
import threading
import uuid
from datetime import UTC, datetime

import psycopg
import pytest
from psycopg import sql
from psycopg.errors import (
    CheckViolation,
    DivisionByZero,
    ExclusionViolation,
    NotNullViolation,
)
from psycopg.rows import dict_row

from purchase import migrations
from purchase.migrations import MIGRATIONS_DIR, run_migrations

PURCHASE_TABLES = {
    "users",
    "spaces",
    "bookings",
    "subscriptions",
    "purchase_schema_migrations",
}


@pytest.fixture
def connect(database_url, monkeypatch):
    """Opens connections whose search_path is one empty scratch schema,
    dropped after the test."""
    monkeypatch.delenv("SEED_STARTER_SPACE", raising=False)
    schema = f"test_migrations_{uuid.uuid4().hex}"
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
        admin.execute(f"CREATE SCHEMA {schema}")
    opened = []

    def open_connection():
        conn = psycopg.connect(
            database_url,
            autocommit=True,
            row_factory=dict_row,
            options=f"-c search_path={schema}",
        )
        opened.append(conn)
        return conn

    yield open_connection
    for conn in opened:
        conn.close()
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA {schema} CASCADE")


@pytest.fixture
def db(connect):
    """A migrated scratch schema with spaces 1 and 2."""
    conn = connect()
    run_migrations(conn)
    conn.execute(
        "INSERT INTO spaces (id, name, capacity) "
        "VALUES (1, 'Room 1', 1), (2, 'Room 2', 1)"
    )
    return conn


def at(hour, minute=0):
    return datetime(2031, 1, 5, hour, minute, tzinfo=UTC)


def insert_booking(conn, **columns) -> dict:
    """A raw INSERT into bookings; returns the stored id and status."""
    row = {
        "space_id": 1,
        "member": "Ann",
        "start_time": at(10),
        "end_time": at(11),
        "amount_cents": 2500,
        "status": "pending",
        **columns,
    }
    query = sql.SQL(
        "INSERT INTO bookings ({}) VALUES ({}) RETURNING id, status"
    ).format(
        sql.SQL(", ").join(map(sql.Identifier, row)),
        sql.SQL(", ").join(sql.Placeholder() * len(row)),
    )
    return conn.execute(query, list(row.values())).fetchone()


def recorded(conn) -> list:
    return [
        row["name"]
        for row in conn.execute(
            "SELECT name FROM purchase_schema_migrations ORDER BY name"
        )
    ]


def table_exists(conn, name) -> bool:
    return conn.execute("SELECT to_regclass(%s) AS t", (name,)).fetchone()["t"]


def sql_files() -> list:
    return sorted(path.name for path in MIGRATIONS_DIR.glob("*.sql"))


def test_migration_files_have_unique_three_digit_numbers():
    names = sql_files()

    assert names
    for name in names:
        assert re.fullmatch(r"\d{3}_[a-z0-9_]+\.sql", name), name
    numbers = [name[:3] for name in names]
    assert len(numbers) == len(set(numbers))


def test_migrations_create_only_purchase_tables(connect):
    conn = connect()

    run_migrations(conn)

    tables = conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = current_schema()"
    ).fetchall()
    assert {row["table_name"] for row in tables} == PURCHASE_TABLES


def test_a_second_run_applies_nothing(connect):
    conn = connect()
    run_migrations(conn)
    query = "SELECT name, applied_at FROM purchase_schema_migrations ORDER BY name"
    before = conn.execute(query).fetchall()

    run_migrations(conn)

    assert conn.execute(query).fetchall() == before
    assert recorded(conn) == sql_files()


def test_a_recorded_migration_without_a_file_is_ignored(connect):
    conn = connect()
    run_migrations(conn)
    conn.execute(
        "INSERT INTO purchase_schema_migrations (name) VALUES ('999_newer.sql')"
    )

    run_migrations(conn)

    assert recorded(conn) == [*sql_files(), "999_newer.sql"]


def test_a_failing_file_is_rolled_back_and_not_recorded(connect, monkeypatch, tmp_path):
    (tmp_path / "001_ok.sql").write_text("CREATE TABLE ok (id INTEGER);")
    bad = tmp_path / "002_bad.sql"
    bad.write_text("CREATE TABLE bad (id INTEGER);\nSELECT 1/0;")
    monkeypatch.setattr(migrations, "MIGRATIONS_DIR", tmp_path)
    conn = connect()

    with pytest.raises(DivisionByZero):
        run_migrations(conn)

    assert recorded(conn) == ["001_ok.sql"]
    assert not table_exists(conn, "bad")

    bad.write_text("CREATE TABLE bad (id INTEGER);")
    second = connect()
    second.execute("SET lock_timeout = '1s'")
    run_migrations(second)

    assert recorded(second) == ["001_ok.sql", "002_bad.sql"]


def test_a_file_whose_record_fails_is_rolled_back(connect, monkeypatch, tmp_path):
    (tmp_path / "001_ok.sql").write_text("CREATE TABLE ok (id INTEGER);")
    monkeypatch.setattr(migrations, "MIGRATIONS_DIR", tmp_path)
    conn = connect()
    conn.execute(
        "CREATE TABLE purchase_schema_migrations ("
        "name TEXT PRIMARY KEY CHECK (name <> '001_ok.sql'), "
        "applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
    )

    with pytest.raises(CheckViolation):
        run_migrations(conn)

    assert not table_exists(conn, "ok")


def test_two_runners_at_once_apply_each_file_once_and_seed_once(connect, monkeypatch):
    monkeypatch.setenv("SEED_STARTER_SPACE", "true")
    conns = [connect(), connect()]
    barrier = threading.Barrier(len(conns))
    errors = []

    def run(conn):
        barrier.wait()
        try:
            run_migrations(conn)
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=run, args=(conn,)) for conn in conns]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert recorded(conns[0]) == sql_files()
    count = conns[0].execute("SELECT COUNT(*) AS count FROM spaces").fetchone()
    assert count["count"] == 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, []),
        ("false", []),
        ("true", [{"name": "Founders Desk", "capacity": 1, "price_cents": 2500}]),
    ],
    ids=["unset", "false", "true"],
)
def test_the_starter_space_is_seeded_only_when_asked(
    connect, monkeypatch, value, expected
):
    if value is not None:
        monkeypatch.setenv("SEED_STARTER_SPACE", value)
    conn = connect()

    run_migrations(conn)
    run_migrations(conn)

    spaces = conn.execute("SELECT name, capacity, price_cents FROM spaces").fetchall()
    assert spaces == expected


@pytest.mark.parametrize("status", ["pending", "completed", "expired", "cancelled"])
def test_a_booking_takes_each_of_the_four_statuses(db, status):
    booking = insert_booking(db, status=status)

    assert booking["status"] == status


@pytest.mark.parametrize(
    ("status", "error"),
    [
        ("paid", CheckViolation),
        ("unpaid", CheckViolation),
        ("PENDING", CheckViolation),
        (None, NotNullViolation),
    ],
    ids=["paid", "unpaid", "PENDING", "NULL"],
)
def test_a_booking_rejects_any_other_status(db, status, error):
    with pytest.raises(error):
        insert_booking(db, status=status)


@pytest.mark.parametrize("column", ["start_time", "end_time", "amount_cents"])
def test_a_booking_requires_times_and_amount(db, column):
    with pytest.raises(NotNullViolation):
        insert_booking(db, **{column: None})


@pytest.mark.parametrize("status", ["pending", "completed"])
def test_an_overlapping_booking_is_rejected_while_the_slot_is_held(db, status):
    insert_booking(db, status=status)

    with pytest.raises(ExclusionViolation):
        insert_booking(db, start_time=at(10, 30), end_time=at(11, 30))


@pytest.mark.parametrize("status", ["expired", "cancelled"])
def test_an_expired_or_cancelled_booking_frees_its_slot(db, status):
    insert_booking(db, status=status)

    booking = insert_booking(db)

    assert booking["status"] == "pending"


def test_back_to_back_and_other_space_bookings_are_allowed(db):
    insert_booking(db, status="completed")

    back_to_back = insert_booking(db, start_time=at(11), end_time=at(12))
    other_space = insert_booking(db, space_id=2)

    assert back_to_back["status"] == "pending"
    assert other_space["status"] == "pending"


def test_rows_keep_the_ids_they_are_inserted_with(db):
    booking = insert_booking(db, id=1149)

    assert booking["id"] == 1149
