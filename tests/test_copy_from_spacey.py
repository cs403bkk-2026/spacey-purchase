import os
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.errors import RaiseException, ReadOnlySqlTransaction
from psycopg.rows import dict_row

from purchase.migrations import run_migrations
from scripts.copy_from_spacey import anomaly, main, map_booking
from tests.fixtures.spacey_schema_f34a4b8 import create_tables

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
    server = os.environ["DATABASE_URL"]
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
    return make_migrated_db(make_db)


def make_migrated_db(make_db) -> str:
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


def freeze_then_unfreeze(url):
    freeze(url)
    run_sql_file(url, "unfreeze.sql")


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
            freeze_then_unfreeze,
            "refused: old is not frozen (run scripts/freeze.sql on OLD_URL)",
        ),
        (freeze_with_an_anomaly, "anomaly booking 4: amount_cents is NULL"),
    ],
    ids=["not frozen", "unfrozen again", "anomaly"],
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


def switch_state(url):
    return fetch(url, "SELECT state FROM purchase_switch")[0]["state"]


def test_switch_on_needs_the_freeze(old_url):
    with pytest.raises(RaiseException, match="not frozen"):
        run_sql_file(old_url, "switch_on.sql")
    assert switch_state(old_url) == "off"

    freeze(old_url)
    run_sql_file(old_url, "switch_on.sql")
    run_sql_file(old_url, "switch_on.sql")

    assert switch_state(old_url) == "on"


def test_unfreeze_waits_for_the_rollback(old_url):
    freeze(old_url)
    run_sql_file(old_url, "switch_on.sql")

    with pytest.raises(RaiseException, match="switch is on"):
        run_sql_file(old_url, "unfreeze.sql")
    assert switch_state(old_url) == "on"

    freeze(old_url)  # the rollback's first step
    run_sql_file(old_url, "unfreeze.sql")
    run_sql_file(old_url, "unfreeze.sql")
    assert switch_state(old_url) == "off"


def test_freeze_waits_for_a_booking_in_flight(old_url):
    """A booking locks bookings first, then its key's row in spaces. The freeze
    takes bookings first too, or the two deadlock and the booking fails."""
    errors = []

    def freeze_in_background():
        try:
            freeze(old_url)
        except psycopg.Error as error:
            errors.append(type(error).__name__)

    with psycopg.connect(old_url) as conn:
        conn.execute("UPDATE bookings SET member = member WHERE id = 3")
        thread = threading.Thread(target=freeze_in_background)
        thread.start()
        time.sleep(0.5)  # the freeze now waits for this transaction
        conn.execute(
            "INSERT INTO bookings (space_id, member, start_time, end_time, "
            "amount_cents, paid) VALUES (3, 'cy', %s, %s, 1000, FALSE)",
            (at(14), at(15)),
        )
        conn.commit()
    thread.join(10)

    assert errors == []
    assert switch_state(old_url) == "frozen"


def test_repair_waits_for_a_mirror_write_in_flight(old_url, new_url):
    """The mirror's write locks bookings first, then its key's row in spaces.
    The repair takes bookings first too, or the two deadlock."""
    copy(old_url, new_url)
    errors = []

    def repair_in_background():
        try:
            main([old_url, new_url, "--repair"])
        except psycopg.Error as error:
            errors.append(type(error).__name__)

    with psycopg.connect(old_url) as conn:
        conn.execute("SET LOCAL purchase.mirror = 'on'")
        conn.execute("UPDATE bookings SET member = member WHERE id = 3")
        thread = threading.Thread(target=repair_in_background)
        thread.start()
        time.sleep(0.5)  # the repair now waits for this transaction
        conn.execute(
            "INSERT INTO bookings (space_id, member, start_time, end_time, "
            "amount_cents, paid) VALUES (3, 'cy', %s, %s, 1000, FALSE)",
            (at(14), at(15)),
        )
        conn.commit()
    thread.join(10)

    assert errors == []


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


def make_changes(url):
    """In new after a copy: a new user, a new space with a pending booking,
    and booking 3 paid with card 4242."""
    execute(
        url,
        "INSERT INTO users (email, password_hash) VALUES ('cy@example.com', 'hash-cy')",
    )
    space = fetch(
        url,
        "INSERT INTO spaces (name, capacity, price_cents) "
        "VALUES ('Room 4', 2, 3000) RETURNING id",
    )[0]["id"]
    insert_new_booking(url, space, at(10), at(11), "pending")
    execute(
        url,
        "UPDATE bookings SET status = 'completed', card_last4 = '4242' WHERE id = 3",
    )


def insert_new_booking(url, space_id, start, end, status) -> int:
    return fetch(
        url,
        "INSERT INTO bookings (space_id, member, start_time, end_time, "
        "amount_cents, status) VALUES (%s, 'cy', %s, %s, 2500, %s) RETURNING id",
        (space_id, start, end, status),
    )[0]["id"]


def set_status(url, booking_id, status):
    execute(url, "UPDATE bookings SET status = %s WHERE id = %s", (status, booking_id))


def old_bookings_from_noon(url) -> list:
    """(id, paid) of old's bookings on space 1 from 12:00, booking 3's slot."""
    rows = fetch(
        url,
        "SELECT id, paid FROM bookings WHERE space_id = 1 AND start_time >= %s "
        "ORDER BY id",
        (at(12),),
    )
    return [(row["id"], row["paid"]) for row in rows]


def as_old_booking(row) -> dict:
    old = {k: v for k, v in row.items() if k != "status"}
    old["paid"] = row["status"] == "completed"
    return old


NOTHING_REPAIRED = [
    "users: inserted 0, updated 0, deleted 0",
    "spaces: inserted 0, updated 0, deleted 0",
    "bookings: inserted 0, updated 0, deleted 0",
]


def test_repair_refuses_unless_old_is_frozen(old_url, new_url, capsys):
    copy(old_url, new_url)
    make_changes(new_url)
    run_sql_file(old_url, "unfreeze.sql")
    capsys.readouterr()
    before = table_rows(old_url)

    code, lines = run(capsys, old_url, new_url, "--repair")

    assert code == 1
    assert lines == ["refused: --repair needs OLD_URL frozen (run scripts/freeze.sql)"]
    assert table_rows(old_url) == before


def test_repair_copies_new_and_changed_rows(old_url, new_url, capsys):
    copy(old_url, new_url)
    make_changes(new_url)

    assert main([old_url, new_url, "--repair"]) == 0

    old, new = table_rows(old_url), table_rows(new_url)
    assert old["users"] == new["users"]
    assert old["spaces"] == new["spaces"]
    assert old["bookings"] == [as_old_booking(row) for row in new["bookings"]]
    assert [row["paid"] for row in old["bookings"]] == [True, True, True, False]
    assert old["bookings"][2]["card_last4"] == "4242"


def test_repair_keeps_an_expired_hold_until_its_slot_is_taken(old_url, new_url):
    copy(old_url, new_url)
    set_status(new_url, 3, "expired")

    assert main([old_url, new_url, "--repair"]) == 0
    assert old_bookings_from_noon(old_url) == [(3, False)]

    newer = insert_new_booking(new_url, 1, at(12), at(13), "expired")
    assert main([old_url, new_url, "--repair"]) == 0
    assert old_bookings_from_noon(old_url) == [(newer, False)]

    set_status(new_url, 3, "completed")
    assert main([old_url, new_url, "--repair"]) == 0
    assert old_bookings_from_noon(old_url) == [(3, True)]


def test_repair_frees_a_cancelled_slot(old_url, new_url):
    copy(old_url, new_url)
    set_status(new_url, 3, "cancelled")
    newer = insert_new_booking(new_url, 1, at(12), at(13), "pending")

    assert main([old_url, new_url, "--repair"]) == 0

    assert old_bookings_from_noon(old_url) == [(newer, False)]


def test_repair_deletes_rows_gone_from_new(old_url, new_url):
    copy(old_url, new_url)
    execute(new_url, "DELETE FROM bookings WHERE space_id = 3")
    execute(new_url, "DELETE FROM spaces WHERE id = 3")

    assert main([old_url, new_url, "--repair"]) == 0

    old = table_rows(old_url)
    assert [row["id"] for row in old["spaces"]] == [1]
    assert [row["id"] for row in old["bookings"]] == [1, 3]


def test_repair_compares_a_users_email_only(old_url, new_url, capsys):
    copy(old_url, new_url)
    execute_as_mirror(old_url, "UPDATE users SET password_hash = 'rehashed'")
    capsys.readouterr()

    code, lines = run(capsys, old_url, new_url, "--repair")

    assert code == 0
    assert "users: inserted 0, updated 0, deleted 0" in lines


def test_repair_leaves_subscriptions_alone(old_url, new_url):
    copy(old_url, new_url)
    execute(new_url, "DELETE FROM subscriptions WHERE member = 'bob'")

    assert main([old_url, new_url, "--repair"]) == 0

    members = fetch(old_url, "SELECT member FROM subscriptions ORDER BY member")
    assert members == [{"member": "ann"}, {"member": "bob"}]


def test_second_repair_changes_nothing(old_url, new_url, capsys):
    copy(old_url, new_url)
    make_changes(new_url)
    assert main([old_url, new_url, "--repair"]) == 0
    capsys.readouterr()

    code, lines = run(capsys, old_url, new_url, "--repair")

    assert code == 0
    assert lines == NOTHING_REPAIRED


def test_old_ids_continue_above_new_ones(old_url, new_url):
    copy(old_url, new_url)
    make_changes(new_url)
    assert main([old_url, new_url, "--repair"]) == 0
    run_sql_file(old_url, "unfreeze.sql")

    inserted = fetch(
        old_url,
        "INSERT INTO bookings (space_id, member, start_time, end_time, "
        "amount_cents, paid) VALUES (1, 'dee', %s, %s, 2500, FALSE) RETURNING id",
        (at(14), at(15)),
    )[0]["id"]

    new_ids = [row["id"] for row in fetch(new_url, "SELECT id FROM bookings")]
    assert inserted > max(new_ids)


def test_repair_then_apply_into_an_empty_db_verifies(old_url, new_url, make_db, capsys):
    copy(old_url, new_url)
    make_changes(new_url)
    assert main([old_url, new_url, "--repair"]) == 0
    third_url = make_migrated_db(make_db)

    assert main([old_url, third_url, "--apply"]) == 0
    capsys.readouterr()

    assert run(capsys, old_url, third_url, "--verify") == (0, ["VERIFY OK"])
