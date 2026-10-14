"""Copy spacey's Purchase data from its old tables into the new database,
and repair the old tables from the new database.

Usage:
    copy_from_spacey.py OLD_URL NEW_URL (--dry-run | --apply | --verify |
                                         --repair)

OLD_URL is spacey's database, which holds the old tables. NEW_URL is the
Purchase database, built by the service's migrations.

Modes:
    --dry-run  Print what a copy would do, and write nothing. Exits 1 when a
               booking is an anomaly.
    --apply    Copy old into new in one transaction. Refuses, writing
               nothing, unless scripts/freeze.sql ran on OLD_URL and no
               booking is an anomaly. A second run inserts nothing.
    --verify   Print VERIFY OK and exit 0 when new holds every old row with
               its mapped values. Otherwise print each difference and exit 1.
               Run it right after --apply, before the switch adds rows.
    --repair   Make the old tables match new under the mirror rules, in one
               old-DB transaction that sets the mirror flag. Refuses, writing
               nothing, unless scripts/freeze.sql ran on OLD_URL. A second
               run changes nothing.

Copy mapping (old -> new):
    users, spaces, subscriptions   same rows, same keys
    booking paid = true            status 'completed', including those a
                                   subscription covered (0, no card)
    booking paid = false, made     status 'expired'
      15 minutes or more before
      the copy (old's clock)
    booking paid = false, newer    status 'pending'
    other booking columns          as they are
    NULL start, end or amount,     anomaly: --apply refuses
      or end <= start
    access, payments               never read
    id sequence last value         new sequence = GREATEST(current,
                                   last value + 100000)

--verify accepts one drift: a booking the mapping makes 'expired' that new
still has as 'pending'. Its hold ran out after --apply, and the service
expires it at its next read.

Mirror rules (new -> old), which the mirror and --repair both follow:
    users row                      same id and email. The mirror hashes the
                                   same password itself, so the hash
                                   differs, but both verify
    spaces row                     same row, same id
    booking 'pending'              row with paid = false
    booking 'completed'            row with paid = true and its card_last4
    booking 'expired'              stays an unpaid row until an overlapping
                                   booking on the same space either holds
                                   its slot ('pending' or 'completed') or
                                   has a higher id. Then it is deleted, and
                                   nothing puts it back: old's overlap
                                   constraint counts every row
    booking 'cancelled'            row deleted, with its access code
    subscriptions                  untouched
    id sequences                   the mirror never moves them; --repair
                                   moves each to GREATEST(old, new)

A paid old row is never deleted for a newer booking. So on the old tables,
the booking count and the unpaid count leave out each expired hold whose
slot was booked again. The paid count and revenue equal the service's
/metrics.

Logins:
    --dry-run and --verify only read, so this is enough on both databases:
        GRANT SELECT ON ALL TABLES IN SCHEMA public TO <role>;
        GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO <role>;
    Table grants do not cover sequences.
    --apply writes the new database. scripts/freeze.sql,
    scripts/unfreeze.sql and --repair need the old tables' owner.
"""

import argparse
from collections import Counter
from datetime import datetime, timedelta

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

HOLD = timedelta(minutes=15)  # rule SP-R07, the same hold the service applies
SEQUENCE_GAP = 100000

# Each table's columns, key first. The tables are in foreign-key order.
OLD_COLUMNS = {
    "users": ("id", "email", "password_hash", "created_at"),
    "spaces": ("id", "name", "capacity", "price_cents"),
    "subscriptions": ("member", "active", "started_at"),
    "bookings": (
        "id",
        "space_id",
        "member",
        "user_id",
        "start_time",
        "end_time",
        "amount_cents",
        "paid",
        "card_last4",
        "created_at",
    ),
}
NEW_COLUMNS = {
    **OLD_COLUMNS,
    "bookings": tuple(
        "status" if column == "paid" else column for column in OLD_COLUMNS["bookings"]
    ),
}
SEQUENCE_TABLES = ("users", "spaces", "bookings")

# The freeze is on only when all 6 rows come back: both triggers on each of
# users, spaces and bookings.
# The hub's switch (PUR-025): 'frozen' and 'on' both freeze the old tables.
SWITCH_STATE = "SELECT state FROM purchase_switch"

# 0 when the sequence has handed out no id yet.
SEQUENCE_LAST_VALUE = """
SELECT COALESCE(pg_sequence_last_value(
    pg_get_serial_sequence(%s, 'id')::regclass), 0) AS value
"""
RAISE_SEQUENCE = """
SELECT setval(pg_get_serial_sequence(%(table)s, 'id')::regclass, GREATEST(
    COALESCE(pg_sequence_last_value(
        pg_get_serial_sequence(%(table)s, 'id')::regclass), 0),
    %(floor)s)) AS value
"""

# The bookings the old table should hold, under the mirror rules. The overlap
# test is the one old's constraint uses: start < other end and end > other start.
MIRRORED_BOOKINGS = """
SELECT id, space_id, member, user_id, start_time, end_time, amount_cents,
       status = 'completed' AS paid, card_last4, created_at
FROM bookings b
WHERE status IN ('pending', 'completed')
   OR (status = 'expired' AND NOT EXISTS (
       SELECT 1 FROM bookings x
       WHERE x.space_id = b.space_id AND x.id <> b.id
         AND x.start_time < b.end_time AND x.end_time > b.start_time
         AND (x.status IN ('pending', 'completed') OR x.id > b.id)))
"""

# The columns --repair compares on a row both sides hold. It inserts every
# column, so a user's password_hash and created_at are written only once: the
# mirror hashes the same password itself, and the hashes differ.
REPAIR_COMPARED_COLUMNS = {
    "users": ("email",),
    "spaces": ("name", "capacity", "price_cents"),
    "bookings": OLD_COLUMNS["bookings"][1:],
}


def map_booking(row: dict, now: datetime) -> dict:
    """Old booking row -> new booking row. now: old's clock at the copy."""
    new = {k: v for k, v in row.items() if k != "paid"}
    if row["paid"]:
        new["status"] = "completed"
    elif row["created_at"] <= now - HOLD:
        new["status"] = "expired"
    else:
        new["status"] = "pending"
    return new


def anomaly(row: dict) -> str | None:
    """Why the new schema would refuse this old booking, or None."""
    for column in ("start_time", "end_time", "amount_cents"):
        if row[column] is None:
            return f"{column} is NULL"
    if row["end_time"] <= row["start_time"]:
        return "end_time <= start_time"
    return None


def list_anomalies(bookings: dict) -> list[str]:
    return [
        f"anomaly booking {booking_id}: {reason}"
        for booking_id, row in bookings.items()
        if (reason := anomaly(row))
    ]


def calculate_new_sequence(old_last: int, new_last: int) -> int:
    return max(new_last, old_last + SEQUENCE_GAP)


def connect_read_only(url: str) -> psycopg.Connection:
    """One read-only REPEATABLE READ transaction: every read sees the same
    moment."""
    conn = psycopg.connect(url, row_factory=dict_row)
    conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
    conn.read_only = True
    return conn


def read_tables(conn, columns: dict) -> dict:
    """Every row of each table, as {table: {key: row}}."""
    tables = {}
    for table, names in columns.items():
        query = sql.SQL("SELECT {} FROM {} ORDER BY {}").format(
            sql.SQL(", ").join(map(sql.Identifier, names)),
            sql.Identifier(table),
            sql.Identifier(names[0]),
        )
        tables[table] = {row[names[0]]: row for row in conn.execute(query)}
    return tables


def read_sequences(conn) -> dict:
    return {
        table: conn.execute(SEQUENCE_LAST_VALUE, (table,)).fetchone()["value"]
        for table in SEQUENCE_TABLES
    }


def read_old(url: str) -> dict:
    """The old tables, their sequences, whether the freeze is on and now(),
    from one snapshot on old's clock."""
    with connect_read_only(url) as conn:
        state = conn.execute(SWITCH_STATE).fetchone()["state"]
        return {
            "tables": read_tables(conn, OLD_COLUMNS),
            "sequences": read_sequences(conn),
            "frozen": state != "off",
            "now": conn.execute("SELECT now() AS now").fetchone()["now"],
        }


def read_new(url: str) -> dict:
    with connect_read_only(url) as conn:
        return {
            "tables": read_tables(conn, NEW_COLUMNS),
            "sequences": read_sequences(conn),
        }


def mapped_tables(old: dict) -> dict:
    """The old rows in the new shape, as {table: {key: row}}."""
    bookings = {
        booking_id: map_booking(row, old["now"])
        for booking_id, row in old["tables"]["bookings"].items()
    }
    return {**old["tables"], "bookings": bookings}


def dry_run(old: dict, new: dict) -> int:
    """Prints the counts a copy would give; returns 1 when a booking is an
    anomaly, else 0."""
    for table in OLD_COLUMNS:
        print(
            f"rows {table}: old {len(old['tables'][table])}, "
            f"new {len(new['tables'][table])}"
        )
    bookings = mapped_tables(old)["bookings"].values()
    statuses = Counter(booking["status"] for booking in bookings)
    future_slots = sum(
        1
        for booking in bookings
        if booking["status"] == "expired"
        and booking["end_time"] is not None
        and booking["end_time"] > old["now"]
    )
    print(f"paid=true -> completed {statuses['completed']}")
    print(
        f"paid=false -> expired {statuses['expired']} "
        f"({future_slots} future slots free)"
    )
    print(f"paid=false -> pending {statuses['pending']}")
    for table in SEQUENCE_TABLES:
        old_last, new_last = old["sequences"][table], new["sequences"][table]
        print(
            f"sequence {table}: old {old_last}, new {new_last} "
            f"(apply sets {calculate_new_sequence(old_last, new_last)})"
        )
    problems = list_anomalies(old["tables"]["bookings"])
    if problems:
        print("\n".join(problems))
        return 1
    print("anomalies: none")
    return 0


def insert_rows(conn, table: str, rows) -> int:
    """Inserts each row unless it clashes with one already there; returns
    how many were inserted."""
    columns = NEW_COLUMNS[table]
    query = sql.SQL("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT DO NOTHING").format(
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, columns)),
        sql.SQL(", ").join(sql.Placeholder() * len(columns)),
    )
    return sum(
        conn.execute(query, [row[column] for column in columns]).rowcount
        for row in rows
    )


def apply(old: dict, new_url: str) -> int:
    """Copies old into new in one transaction; returns 0, or 1 when it
    refused and wrote nothing."""
    if not old["frozen"]:
        # A booking can be paid until the freeze, and a second --apply never
        # updates a row already copied.
        print("refused: old is not frozen (run scripts/freeze.sql on OLD_URL)")
        return 1
    problems = list_anomalies(old["tables"]["bookings"])
    if problems:
        print("\n".join(problems))
        return 1
    with psycopg.connect(new_url, row_factory=dict_row) as conn:
        inserted = {
            table: insert_rows(conn, table, rows.values())
            for table, rows in mapped_tables(old).items()
        }
        sequences = {
            table: conn.execute(
                RAISE_SEQUENCE,
                {"table": table, "floor": old["sequences"][table] + SEQUENCE_GAP},
            ).fetchone()["value"]
            for table in SEQUENCE_TABLES
        }
    for table, count in inserted.items():
        print(f"{table}: inserted {count}")
    for table, value in sequences.items():
        print(f"sequence {table}: {value}")
    return 0


def accept_ran_out_hold(want: dict, stored: dict | None) -> dict:
    """want, but pending when new still has it pending and only the mapping
    says expired: its hold ran out after --apply."""
    if (
        want["status"] == "expired"
        and stored is not None
        and stored["status"] == "pending"
    ):
        return {**want, "status": "pending"}
    return want


def count_differences(want: dict, stored: dict) -> list[str]:
    lines = [
        f"count {table}: new {len(stored[table])}, want {len(rows)}"
        for table, rows in want.items()
        if len(stored[table]) != len(rows)
    ]
    want_statuses = Counter(row["status"] for row in want["bookings"].values())
    stored_statuses = Counter(row["status"] for row in stored["bookings"].values())
    lines += [
        f"count bookings {status}: new {stored_statuses[status]}, "
        f"want {want_statuses[status]}"
        for status in sorted(want_statuses | stored_statuses)
        if stored_statuses[status] != want_statuses[status]
    ]
    return lines


def row_differences(want: dict, stored: dict) -> list[str]:
    lines = []
    for table, rows in want.items():
        for key, row in rows.items():
            stored_row = stored[table].get(key)
            if stored_row is None:
                lines.append(f"{table} {key}: missing in new")
                continue
            lines += [
                f"{table} {key}: {column} is {stored_row[column]}, want {value}"
                for column, value in row.items()
                if stored_row[column] != value
            ]
    return lines


def sequence_differences(old: dict, new: dict) -> list[str]:
    return [
        f"sequence {table}: new {new[table]} < old {old[table]} + {SEQUENCE_GAP}"
        for table in SEQUENCE_TABLES
        if new[table] < old[table] + SEQUENCE_GAP
    ]


def verify(old: dict, new: dict) -> int:
    """Prints VERIFY OK and returns 0 when new holds old under the mapping;
    else prints each difference and returns 1."""
    want = mapped_tables(old)
    stored = new["tables"]
    want["bookings"] = {
        booking_id: accept_ran_out_hold(booking, stored["bookings"].get(booking_id))
        for booking_id, booking in want["bookings"].items()
    }
    differences = [
        *count_differences(want, stored),
        *row_differences(want, stored),
        *sequence_differences(old["sequences"], new["sequences"]),
    ]
    if differences:
        print("\n".join(differences))
        return 1
    print("VERIFY OK")
    return 0


def insert_row(conn, table: str, row: dict) -> None:
    query = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, row)),
        sql.SQL(", ").join(sql.Placeholder() * len(row)),
    )
    conn.execute(query, list(row.values()))


def update_row(conn, table: str, row: dict, columns: tuple) -> None:
    query = sql.SQL("UPDATE {} SET {} WHERE id = %s").format(
        sql.Identifier(table),
        sql.SQL(", ").join(
            sql.SQL("{} = %s").format(sql.Identifier(column)) for column in columns
        ),
    )
    conn.execute(query, [*(row[column] for column in columns), row["id"]])


def delete_rows(conn, table: str, ids) -> int:
    """Deletes the rows with these ids; returns how many it deleted."""
    query = sql.SQL("DELETE FROM {} WHERE id = ANY(%s)").format(sql.Identifier(table))
    return conn.execute(query, (sorted(ids),)).rowcount


def upsert_rows(conn, table: str, want: dict, have: dict) -> tuple[int, int]:
    """Inserts each wanted row old lacks, and updates each one whose compared
    columns differ; returns (inserted, updated)."""
    compared = REPAIR_COMPARED_COLUMNS[table]
    inserted = updated = 0
    for key in sorted(want):
        row = want[key]
        if key not in have:
            insert_row(conn, table, row)
            inserted += 1
        elif any(row[column] != have[key][column] for column in compared):
            update_row(conn, table, row, compared)
            updated += 1
    return inserted, updated


def read_mirror_source(url: str) -> dict:
    """New's users, spaces, the bookings old should hold, and the sequences,
    from one snapshot."""
    with connect_read_only(url) as conn:
        tables = read_tables(conn, {t: OLD_COLUMNS[t] for t in ("users", "spaces")})
        tables["bookings"] = {row["id"]: row for row in conn.execute(MIRRORED_BOOKINGS)}
        return {"tables": tables, "sequences": read_sequences(conn)}


def repair(old_url: str, new_url: str) -> int:
    """Makes the old tables match new under the mirror rules, in one old-DB
    transaction; returns 0."""
    upserted = {}
    with psycopg.connect(old_url, row_factory=dict_row) as conn:
        conn.execute("SET LOCAL purchase.mirror = 'on'")
        # Mirror writes wait for this lock, so a service write the snapshot
        # below misses reaches old after this commits, and is not undone.
        conn.execute("LOCK TABLE users, spaces, bookings IN EXCLUSIVE MODE")
        new = read_mirror_source(new_url)
        old = read_tables(conn, {t: OLD_COLUMNS[t] for t in SEQUENCE_TABLES})
        old_sequences = read_sequences(conn)
        want = new["tables"]
        gone = {table: old[table].keys() - want[table].keys() for table in old}
        # Before any insert: old's overlap constraint counts every row.
        deleted = {"bookings": delete_rows(conn, "bookings", gone["bookings"])}
        for table in SEQUENCE_TABLES:
            upserted[table] = upsert_rows(conn, table, want[table], old[table])
        for table in ("spaces", "users"):
            deleted[table] = delete_rows(conn, table, gone[table])
        for table in SEQUENCE_TABLES:
            if new["sequences"][table] > old_sequences[table]:
                conn.execute(
                    "SELECT setval(pg_get_serial_sequence(%s, 'id')::regclass, %s)",
                    (table, new["sequences"][table]),
                )
    for table in SEQUENCE_TABLES:
        inserted, updated = upserted[table]
        print(
            f"{table}: inserted {inserted}, updated {updated}, deleted {deleted[table]}"
        )
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="copy_from_spacey.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("old_url", metavar="OLD_URL", help="spacey's database")
    parser.add_argument("new_url", metavar="NEW_URL", help="the Purchase database")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="count, write nothing")
    mode.add_argument("--apply", action="store_true", help="copy old into new")
    mode.add_argument("--verify", action="store_true", help="check new against old")
    mode.add_argument("--repair", action="store_true", help="make old match new")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Runs the chosen mode; returns the exit code."""
    args = parse_args(argv)
    old = read_old(args.old_url)
    if args.repair:
        if not old["frozen"]:
            # Unfrozen old tables mean spacey writes them itself, and the
            # repair would delete those writes.
            print("refused: --repair needs OLD_URL frozen (run scripts/freeze.sql)")
            return 1
        return repair(args.old_url, args.new_url)
    if args.apply:
        return apply(old, args.new_url)
    new = read_new(args.new_url)
    if args.dry_run:
        return dry_run(old, new)
    return verify(old, new)


if __name__ == "__main__":
    raise SystemExit(main())
