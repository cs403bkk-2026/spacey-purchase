"""Database tables: created at start-up if missing, so a new or older
database ends up with every table and column the app needs."""


def create_tables(cur) -> None:
    """Create or upgrade every table the app uses. Safe to run on every
    start: each statement is IF NOT EXISTS or only fills in missing data.
    Tables other contexts use live here for now (subscriptions,
    bookings.card_last4); payments and access have their own migrations."""
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS spaces (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            capacity INTEGER NOT NULL
        )
        """
    )
    cur.execute(
        "ALTER TABLE spaces ADD COLUMN IF NOT EXISTS "
        "price_cents INTEGER NOT NULL DEFAULT 0"
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS bookings (
            id SERIAL PRIMARY KEY,
            space_id INTEGER NOT NULL REFERENCES spaces (id),
            member TEXT NOT NULL,
            paid BOOLEAN NOT NULL
        )
        """
    )
    # Mocked subscriptions, keyed by the trimmed lower-case member name.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS subscriptions (
            member TEXT PRIMARY KEY,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            started_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # First step of #120 (real accounts): just registration for now.
    # Storing only a hash, never the password itself.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # Added after the table already existed, so ALTER instead of editing
    # CREATE TABLE above - existing databases get the new columns too.
    cur.execute(
        "ALTER TABLE bookings "
        "ADD COLUMN IF NOT EXISTS start_time TIMESTAMPTZ, "
        "ADD COLUMN IF NOT EXISTS end_time TIMESTAMPTZ"
    )
    # Price charged at booking time, so a later price change can't rewrite past revenue.
    cur.execute(
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS amount_cents INTEGER"
    )
    # Links a booking to the account that was logged in when it was made
    # (#134, the first concrete step of #86). NULL for a guest booking
    # made while logged out, and for every booking made before this.
    cur.execute(
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS "
        "user_id INTEGER REFERENCES users (id)"
    )
    # Last 4 digits only (#119) - never the full card number or CVC.
    # NULL until the booking is actually paid.
    cur.execute(
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS card_last4 TEXT"
    )
    # When the booking was made (not when the space is used), so the
    # dashboard can show growth over time. Bookings made before this
    # column existed get the time the column was added - the best we
    # have, and it keeps the column NOT NULL.
    cur.execute(
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
    )
    # Bookings from before that column existed get the space's current price (best we have).
    cur.execute(
        "UPDATE bookings SET amount_cents = s.price_cents "
        "FROM spaces s "
        "WHERE s.id = bookings.space_id AND bookings.amount_cents IS NULL"
    )
    # Belt-and-suspenders against double-booking: the app already checks
    # for overlaps before inserting, but that check-then-insert isn't
    # atomic, so two simultaneous requests could both pass the check.
    # This constraint makes Postgres itself reject the second insert.
    cur.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    cur.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'no_overlapping_bookings'
            ) THEN
                ALTER TABLE bookings
                ADD CONSTRAINT no_overlapping_bookings
                EXCLUDE USING gist (
                    space_id WITH =,
                    tstzrange(start_time, end_time) WITH &&
                );
            END IF;
        END $$;
        """
    )
    # PUR-025: the switch, which the Purchase team sets with its own database
    # login: no Nomad variable, no restart. 'off': the old SQL runs, as
    # before. 'frozen': the old SQL still runs, but the three tables below
    # refuse every write except the mirror's and the repair's (their
    # transaction sets purchase.mirror). 'on': with PURCHASE_URL set, the
    # service answers (client.enabled), and the tables stay frozen.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS purchase_switch (
            one BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (one),
            state TEXT NOT NULL DEFAULT 'off'
                CHECK (state IN ('off', 'frozen', 'on'))
        )
        """
    )
    cur.execute("INSERT INTO purchase_switch DEFAULT VALUES ON CONFLICT DO NOTHING")
    cur.execute(
        """
        CREATE OR REPLACE FUNCTION purchase_refuse_write() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF current_setting('purchase.mirror', true) = 'on'
               OR (SELECT state FROM purchase_switch) = 'off' THEN
                IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'purchase: % on % refused: table frozen',
                TG_OP, TG_TABLE_NAME USING ERRCODE = 'read_only_sql_transaction';
        END $$
        """
    )
    for table in ("users", "spaces", "bookings"):
        cur.execute(
            f"CREATE OR REPLACE TRIGGER purchase_frozen "
            f"BEFORE INSERT OR UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION purchase_refuse_write()"
        )
        cur.execute(
            f"CREATE OR REPLACE TRIGGER purchase_frozen_truncate "
            f"BEFORE TRUNCATE ON {table} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION purchase_refuse_write()"
        )
    # Only the Purchase team's logins may change the switch. The database's
    # default privileges give every student login (spacey_students) write
    # access to each new table, so take it back here. Roles that don't exist
    # (a local or CI database) are skipped.
    cur.execute(
        """
        DO $$
        BEGIN
            IF to_regrole('spacey_students') IS NOT NULL THEN
                REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON purchase_switch
                    FROM spacey_students;
            END IF;
            IF to_regrole('team_purchase') IS NOT NULL THEN
                GRANT SELECT, UPDATE ON purchase_switch TO team_purchase;
            END IF;
        END $$
        """
    )
