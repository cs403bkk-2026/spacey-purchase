"""Database tables: created at start-up if missing, so a new or older
database ends up with every table and column the app needs."""


def create_tables(cur) -> None:
    """Create or upgrade every table the app uses. Safe to run on every
    start: each statement is IF NOT EXISTS or only fills in missing data.
    All tables live here for now, including ones other contexts use
    (subscriptions, access, bookings.card_last4)."""
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
    # The access code handed out when a booking is unlocked (#171).
    # One code per booking, kept so a page refresh shows the same code
    # instead of a new one. ON DELETE CASCADE because cancelling a
    # booking deletes its row, and the code is worthless without it.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS access (
            booking_id INTEGER PRIMARY KEY
                REFERENCES bookings (id) ON DELETE CASCADE,
            access_code TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
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
