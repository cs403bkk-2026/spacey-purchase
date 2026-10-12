import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.errors import ReadOnlySqlTransaction
from psycopg.rows import dict_row

from purchase.migrations import run_migrations
from scripts.copy_from_spacey import anomaly, main, map_booking
from tests.fixtures.spacey_schema_88ec849 import create_tables

LOCAL_DATABASE_URL = "postgresql://purchase:purchase@localhost:5435/purchase"
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
TABLES = ("users", "spaces", "subscriptions", "bookings")
SEQUENCE_TABLES = ("users", "spaces", "bookings")
COPY_TIME = datetime(2030, 1, 1, 9, tzinfo=UTC)


def at(hour):
    return datetime(2030, 1, 7, hour, tzinfo=UTC)


@pytest.fixture
def make_db():
    """Creates fresh copy_test_<hex> databases on the DATABASE_URL server,
    each dropped after the test."""
    server = os.getenv("DATABASE_URL", LOCAL_DATABASE_URL)
    names = []

    def make() -> str:
        name = f"copy_test_{uuid.uuid4().hex}"
        with psycopg.connect(server, autocommit=True) as admin:
            admin.execute(f"CREATE DATABASE {name}")
        names.append(name)
        return make_conninfo(server, dbname=name)

    yield make
    with psycopg.connect(server, autocommit=True) as admin:
        for name in names:
            admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


@pytest.fixture
def old_url(make_db):
    """spacey's old tables, holding the made-up data every test starts from."""
    url = make_db()
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        create_tables(cur)
        insert_old_data(cur)
    return url


@pytest.fixture
def new_url(make_db, monkeypatch):
    """An empty, migrated Purchase database: no starter space."""
    monkeypatch.delenv("SEED_STARTER_SPACE", raising=False)
    url = make_db()
    with psycopg.connect(url, autocommit=True, row_factory=dict_row) as conn:
        run_migrations(conn)
    return url


def insert_old_data(cur):
    """Spaces 1 and 3 (2 was deleted), users 1 and 2, an active and an
    inactive subscription, and bookings 1 (paid by card), 2 (covered by a
    subscription) and 3 (unpaid, made just now)."""
    cur.execute(
        "INSERT INTO spaces (name, capacity, price_cents) "
        "VALUES ('Room 1', 4, 2500), ('Room 2', 2, 1500), ('Room 3', 1, 1000)"
    )
    cur.execute("DELETE FROM spaces WHERE id = 2")
    cur.execute(
        "INSERT INTO users (email, password_hash) "
        "VALUES ('ann@example.com', 'hash-ann'), ('bob@example.com', 'hash-bob')"
    )
    cur.execute(
        "INSERT INTO subscriptions (member, active) "
        "VALUES ('ann', TRUE), ('bob', FALSE)"
    )
    cur.execute(
        "INSERT INTO bookings (space_id, member, user_id, start_time, end_time, "
        "amount_cents, paid, card_last4) VALUES "
        "(1, 'ann', 1, %s, %s, 2500, TRUE, '1234'), "
        "(3, 'ann', 1, %s, %s, 0, TRUE, NULL), "
        "(1, 'guest', NULL, %s, %s, 2500, FALSE, NULL)",
        (at(10), at(11), at(10), at(11), at(12), at(13)),
    )


def execute(url, query, params=None):
    with psycopg.connect(url) as conn:
        conn.execute(query, params)


def execute_as_mirror(url, query, params=None):
    """Runs the query in a transaction that sets the mirror flag."""
    with psycopg.connect(url) as conn:
        conn.execute("SET LOCAL purchase.mirror = 'on'")
        conn.execute(query, params)


def fetch(url, query, params=None) -> list:
    with psycopg.connect(url, row_factory=dict_row) as conn:
        return conn.execute(query, params).fetchall()


def run_sql_file(url, name):
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute((SCRIPTS_DIR / name).read_text())


def freeze(url):
    run_sql_file(url, "freeze.sql")


def copy(old_url, new_url):
    freeze(old_url)
    assert main([old_url, new_url, "--apply"]) == 0


def run(capsys, *argv) -> tuple[int, list]:
    """Runs the script; returns its exit code and printed lines."""
    code = main(list(argv))
    return code, capsys.readouterr().out.splitlines()


def table_rows(url) -> dict:
    return {table: fetch(url, f"SELECT * FROM {table} ORDER BY 1") for table in TABLES}


def sequence_value(url, table) -> int | None:
    query = (
        "SELECT pg_sequence_last_value("
        "pg_get_serial_sequence(%s, 'id')::regclass) AS value"
    )
    return fetch(url, query, (table,))[0]["value"]


def sequence_values(url) -> dict:
    return {table: sequence_value(url, table) for table in SEQUENCE_TABLES}


def next_id(url, table) -> int:
    query = "SELECT nextval(pg_get_serial_sequence(%s, 'id')) AS id"
    return fetch(url, query, (table,))[0]["id"]


def set_sequence(url, table, value):
    execute(url, "SELECT setval(pg_get_serial_sequence(%s, 'id'), %s)", (table, value))


def insert_anomaly(url):
    execute(
        url,
        "INSERT INTO bookings (id, space_id, member, start_time, end_time, "
        "amount_cents, paid) VALUES (4, 3, 'cy', %s, %s, NULL, FALSE)",
        (at(14), at(15)),
    )


def old_booking(**columns) -> dict:
    return {
        "id": 1,
        "space_id": 1,
        "member": "ann",
        "user_id": 1,
        "start_time": at(10),
        "end_time": at(11),
        "amount_cents": 2500,
        "paid": False,
        "card_last4": None,
        "created_at": COPY_TIME,
        **columns,
    }


@pytest.mark.parametrize(
    ("columns", "status", "reason"),
    [
        ({"paid": True, "card_last4": "1234"}, "completed", None),
        ({"paid": True, "amount_cents": 0}, "completed", None),
        ({"created_at": COPY_TIME - timedelta(minutes=15)}, "expired", None),
        (
            {"created_at": COPY_TIME - timedelta(minutes=14, seconds=59)},
            "pending",
            None,
        ),
        ({"start_time": None}, "pending", "start_time is NULL"),
        ({"end_time": None}, "pending", "end_time is NULL"),
        ({"amount_cents": None}, "pending", "amount_cents is NULL"),
        ({"end_time": at(10)}, "pending", "end_time <= start_time"),
    ],
    ids=[
        "paid by card",
        "covered",
        "unpaid 15 min",
        "unpaid 14 min 59 s",
        "no start",
        "no end",
        "no amount",
        "end equals start",
    ],
)
def test_map_booking_examples(columns, status, reason):
    row = old_booking(**columns)

    new = map_booking(row, COPY_TIME)

    expected = {**row, "status": status}
    del expected["paid"]
    assert new == expected
    assert anomaly(row) == reason


def test_dry_run_prints_counts_and_writes_nothing(old_url, new_url, capsys):
    before = [(table_rows(url), sequence_values(url)) for url in (old_url, new_url)]

    code, lines = run(capsys, old_url, new_url, "--dry-run")

    assert code == 0
    assert lines == [
        "rows users: old 2, new 0",
        "rows spaces: old 2, new 0",
        "rows subscriptions: old 2, new 0",
        "rows bookings: old 3, new 0",
        "paid=true -> completed 2",
        "paid=false -> expired 0 (0 future slots free)",
        "paid=false -> pending 1",
        "sequence users: old 2, new 0 (apply sets 100002)",
        "sequence spaces: old 3, new 0 (apply sets 100003)",
        "sequence bookings: old 3, new 0 (apply sets 100003)",
        "anomalies: none",
    ]
    after = [(table_rows(url), sequence_values(url)) for url in (old_url, new_url)]
    assert after == before


def test_dry_run_exits_1_on_an_anomaly(old_url, new_url, capsys):
    insert_anomaly(old_url)

    code, lines = run(capsys, old_url, new_url, "--dry-run")

    assert code == 1
    assert "anomaly booking 4: amount_cents is NULL" in lines
    assert "anomalies: none" not in lines


def leave_unfrozen(url):
    pass


def freeze_bookings_only(url):
    freeze(url)
    for table in ("users", "spaces"):
        execute(url, f"DROP TRIGGER purchase_frozen ON {table}")
        execute(url, f"DROP TRIGGER purchase_frozen_truncate ON {table}")


def freeze_with_an_anomaly(url):
    insert_anomaly(url)
    freeze(url)


@pytest.mark.parametrize(
    ("prepare", "message"),
    [
        (
            leave_unfrozen,
            "refused: old is not frozen (run scripts/freeze.sql on OLD_URL)",
        ),
        (
            freeze_bookings_only,
            "refused: old is not frozen (run scripts/freeze.sql on OLD_URL)",
        ),
        (freeze_with_an_anomaly, "anomaly booking 4: amount_cents is NULL"),
    ],
    ids=["not frozen", "bookings only", "anomaly"],
)
def test_apply_refuses_and_writes_nothing(old_url, new_url, capsys, prepare, message):
    prepare(old_url)

    code, lines = run(capsys, old_url, new_url, "--apply")

    assert code == 1
    assert lines == [message]
    assert table_rows(new_url) == {table: [] for table in TABLES}
    assert sequence_values(new_url) == dict.fromkeys(SEQUENCE_TABLES)


def test_apply_copies_with_ids_kept_and_status_mapped(old_url, new_url):
    freeze(old_url)

    assert main([old_url, new_url, "--apply"]) == 0

    old, new = table_rows(old_url), table_rows(new_url)
    for table in ("users", "spaces", "subscriptions"):
        assert new[table] == old[table]
    expected = [
        {**{k: v for k, v in row.items() if k != "paid"}, "status": status}
        for row, status in zip(
            old["bookings"], ["completed", "completed", "pending"], strict=True
        )
    ]
    assert new["bookings"] == expected
    assert [space["id"] for space in new["spaces"]] == [1, 3]


def test_unpaid_older_than_the_hold_is_copied_expired(old_url, new_url, capsys):
    execute(
        old_url,
        "UPDATE bookings SET created_at = now() - interval '15 minutes' WHERE id = 3",
    )

    _, dry_run_lines = run(capsys, old_url, new_url, "--dry-run")
    copy(old_url, new_url)
    capsys.readouterr()
    code, verify_lines = run(capsys, old_url, new_url, "--verify")

    assert "paid=false -> expired 1 (1 future slots free)" in dry_run_lines
    booking = fetch(new_url, "SELECT status FROM bookings WHERE id = 3")
    assert booking == [{"status": "expired"}]
    assert code == 0
    assert verify_lines == ["VERIFY OK"]


def test_verify_accepts_a_hold_that_ran_out_after_the_copy(old_url, new_url, capsys):
    copy(old_url, new_url)
    capsys.readouterr()
    hour_back = "UPDATE bookings SET created_at = created_at - interval '1 hour' "
    execute_as_mirror(old_url, hour_back + "WHERE id = 3")
    execute(new_url, hour_back + "WHERE id = 3")

    code, lines = run(capsys, old_url, new_url, "--verify")

    assert code == 0
    assert lines == ["VERIFY OK"]


def test_second_apply_inserts_nothing(old_url, new_url, capsys):
    copy(old_url, new_url)
    capsys.readouterr()

    code, lines = run(capsys, old_url, new_url, "--apply")

    assert code == 0
    for table in TABLES:
        assert f"{table}: inserted 0" in lines
    assert run(capsys, old_url, new_url, "--verify") == (0, ["VERIFY OK"])


def test_new_ids_start_above_the_gap(old_url, new_url):
    execute(old_url, "UPDATE bookings SET id = 1147 WHERE id = 3")
    for table, value in {"users": 5, "spaces": 13, "bookings": 1149}.items():
        set_sequence(old_url, table, value)

    copy(old_url, new_url)

    assert next_id(new_url, "users") == 100006
    assert next_id(new_url, "spaces") == 100014
    assert next_id(new_url, "bookings") == 101150


def test_apply_never_moves_a_sequence_back(old_url, new_url):
    set_sequence(new_url, "bookings", 200000)

    copy(old_url, new_url)

    assert sequence_value(new_url, "bookings") == 200000


@pytest.mark.parametrize(
    ("change", "difference"),
    [
        (
            "UPDATE bookings SET member = 'mallory' WHERE id = 1",
            "bookings 1: member is mallory, want ann",
        ),
        ("DELETE FROM bookings WHERE id = 3", "bookings 3: missing in new"),
        (
            "SELECT setval(pg_get_serial_sequence('users', 'id'), 2)",
            "sequence users: new 2 < old 2 + 100000",
        ),
    ],
    ids=["changed column", "missing row", "sequence"],
)
def test_verify_names_each_difference(old_url, new_url, capsys, change, difference):
    copy(old_url, new_url)
    capsys.readouterr()
    execute(new_url, change)

    code, lines = run(capsys, old_url, new_url, "--verify")

    assert code == 1
    assert difference in lines
    assert "VERIFY OK" not in lines


# Each write touches a row nothing references: user 2, the spare space 4 and
# booking 3.
WRITES = {
    "users": [
        "INSERT INTO users (email, password_hash) VALUES ('cy@example.com', 'h')",
        "UPDATE users SET email = 'bobby@example.com' WHERE id = 2",
        "DELETE FROM users WHERE id = 2",
        "TRUNCATE users CASCADE",
    ],
    "spaces": [
        "INSERT INTO spaces (name, capacity) VALUES ('Room 5', 1)",
        "UPDATE spaces SET name = 'Room 4' WHERE id = 4",
        "DELETE FROM spaces WHERE id = 4",
        "TRUNCATE spaces CASCADE",
    ],
    "bookings": [
        "INSERT INTO bookings (space_id, member, start_time, end_time, "
        "amount_cents, paid) VALUES "
        "(4, 'cy', '2030-01-07 14:00Z', '2030-01-07 15:00Z', 1000, FALSE)",
        "UPDATE bookings SET member = 'gus' WHERE id = 3",
        "DELETE FROM bookings WHERE id = 3",
        "TRUNCATE bookings CASCADE",
    ],
}


@pytest.mark.parametrize(
    "statement",
    [
        pytest.param(statement, id=f"{table}-{statement.split()[0].lower()}")
        for table, statements in WRITES.items()
        for statement in statements
    ],
)
def test_freeze_refuses_every_write(old_url, statement):
    execute(old_url, "INSERT INTO spaces (name, capacity) VALUES ('Spare', 1)")
    freeze(old_url)
    freeze(old_url)
    before = table_rows(old_url)

    with pytest.raises(ReadOnlySqlTransaction):
        execute(old_url, statement)

    assert table_rows(old_url) == before
    run_sql_file(old_url, "unfreeze.sql")
    execute(old_url, statement)
    assert table_rows(old_url) != before


def test_freeze_lets_the_mirror_flag_through(old_url):
    freeze(old_url)

    with psycopg.connect(old_url, row_factory=dict_row) as conn:
        conn.execute("SET LOCAL purchase.mirror = 'on'")
        inserted = conn.execute(
            "INSERT INTO bookings (space_id, member, start_time, end_time, "
            "amount_cents, paid) VALUES (3, 'cy', %s, %s, 1000, FALSE) RETURNING id",
            (at(14), at(15)),
        ).fetchone()
        updated = conn.execute(
            "UPDATE bookings SET paid = TRUE, card_last4 = '4242' WHERE id = %s",
            (inserted["id"],),
        )
        deleted = conn.execute("DELETE FROM bookings WHERE id = %s", (inserted["id"],))
        conn.commit()

        assert updated.rowcount == 1
        assert deleted.rowcount == 1
        with pytest.raises(ReadOnlySqlTransaction):
            conn.execute("UPDATE bookings SET member = 'gus' WHERE id = 3")
        conn.rollback()


def test_spacey_start_up_runs_on_frozen_tables(old_url):
    freeze(old_url)
    before = table_rows(old_url)

    with psycopg.connect(old_url) as conn, conn.cursor() as cur:
        create_tables(cur)

    assert table_rows(old_url) == before
