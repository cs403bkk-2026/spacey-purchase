"""Booking helpers: pricing, booking a space and finding bookings."""

from datetime import UTC, datetime

from psycopg.errors import DeadlockDetected, ExclusionViolation

from purchase.member import normalise_member_name

BOOKING_COLUMNS = (
    "id, space_id, member, status = 'completed' AS paid, start_time, end_time, "
    "amount_cents, user_id, card_last4, created_at"
)

# SP-R07: an unpaid booking holds its slot for 15 minutes from creation.
# The constraint can't read now(), so code that decides a slot marks
# overdue holds first.
EXPIRE_HOLDS = (
    "UPDATE bookings SET status = 'expired' "
    "WHERE status = 'pending' AND created_at <= now() - interval '15 minutes'"
)


def booking_to_json(row: dict) -> dict:
    """A booking row ready for jsonify: its times as UTC ISO 8601 strings."""
    return {
        **row,
        "start_time": row["start_time"].astimezone(UTC).isoformat(),
        "end_time": row["end_time"].astimezone(UTC).isoformat(),
        "created_at": row["created_at"].astimezone(UTC).isoformat(),
    }


def calculate_booking_price_cents(
    hourly_rate_cents: int, start_time: datetime, end_time: datetime
) -> int:
    """Prorate an hourly rate, rounding half cents up.

    The caller supplies a validated interval.
    Duration is truncated to whole seconds, preserving the existing calculation.
    """
    seconds = int((end_time - start_time).total_seconds())
    return (hourly_rate_cents * seconds + 1800) // 3600


def create_booking(cur, space_id, member, start_time, end_time, party_size, user_id):
    """Book a space for a member; user_id is the logged-in account or None.
    member is the name as sent: trimmed, "guest" if missing or blank.
    Returns (payload, status) - the raw booking row, or an {"error": ...}."""
    cur.execute(
        "SELECT id, capacity, price_cents FROM spaces WHERE id = %s",
        (space_id,),
    )
    space = cur.fetchone()
    if space is None:
        return {"error": "space not found"}, 404

    member = normalise_member_name(member)
    if member is None:
        return {"error": "member must be a string"}, 400

    if end_time <= start_time:
        return {"error": "end_time must be after start_time"}, 400
    # bool is a subclass of int in Python, so rule out true/false
    if (
        not isinstance(party_size, int)
        or isinstance(party_size, bool)
        or party_size < 1
    ):
        return {"error": "party_size must be a whole number of at least 1"}, 400
    if party_size > space["capacity"]:
        return {
            "error": f"party_size {party_size} exceeds this space's "
            f"capacity of {space['capacity']}"
        }, 400

    # The account the hub has logged in, or None for a guest booking.
    if user_id is not None:
        if not isinstance(user_id, int) or isinstance(user_id, bool):
            return {"error": "user_id must be a whole number or null"}, 400
        cur.execute("SELECT 1 FROM users WHERE id = %s", (user_id,))
        if cur.fetchone() is None:
            return {"error": "user_id is not a known account"}, 400

    # Overlap = starts before the other ends AND ends after the
    # other starts. Back-to-back bookings (10-11, 11-12) are allowed.
    cur.execute(EXPIRE_HOLDS)
    cur.execute(
        "SELECT id FROM bookings "
        "WHERE space_id = %s AND start_time < %s AND end_time > %s "
        "AND status IN ('pending', 'completed')",
        (space_id, end_time, start_time),
    )
    if cur.fetchone() is not None:
        return {"error": "space is already booked for that time"}, 409

    # Always unpaid at its calculated price.
    try:
        cur.execute(
            "INSERT INTO bookings "
            "(space_id, member, status, start_time, end_time, "
            "amount_cents, user_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) "
            f"RETURNING {BOOKING_COLUMNS}",
            (
                space_id,
                member,
                "pending",
                start_time,
                end_time,
                calculate_booking_price_cents(
                    space["price_cents"], start_time, end_time
                ),
                user_id,
            ),
        )
    except (DeadlockDetected, ExclusionViolation):
        # The pre-check above already caught this in the common
        # case; this only fires when two requests raced past it.
        return {"error": "space is already booked for that time"}, 409
    return cur.fetchone(), 201


def list_bookings(cur) -> list:
    cur.execute(
        f"SELECT {BOOKING_COLUMNS} FROM bookings "
        "WHERE status <> 'cancelled' ORDER BY start_time"
    )
    return cur.fetchall()


def list_space_bookings(cur, space_id) -> list:
    cur.execute(EXPIRE_HOLDS)
    cur.execute(
        f"SELECT {BOOKING_COLUMNS} FROM bookings "
        "WHERE space_id = %s AND status IN ('pending', 'completed') "
        "ORDER BY start_time",
        (space_id,),
    )
    return cur.fetchall()


def list_user_bookings(cur, user_id) -> list:
    """Bookings made while logged in as user_id; guest bookings never match."""
    cur.execute(
        f"SELECT {BOOKING_COLUMNS} FROM bookings "
        "WHERE user_id = %s AND status <> 'cancelled' ORDER BY start_time",
        (user_id,),
    )
    return cur.fetchall()


def get_booking(cur, booking_id) -> dict | None:
    cur.execute(
        f"SELECT {BOOKING_COLUMNS} FROM bookings "
        "WHERE id = %s AND status <> 'cancelled'",
        (booking_id,),
    )
    return cur.fetchone()


def check_unlockable(cur, booking_id) -> tuple[dict, int] | None:
    """Purchase's check before Access issues a code: the booking must exist
    and be paid. Returns (payload, status) to answer with, or None if fine."""
    booking = get_booking(cur, booking_id)
    if booking is None:
        return {"error": "booking not found"}, 404
    if not booking["paid"]:
        return {"error": "booking is not paid"}, 402
    return None


def check_payable(cur, booking_id):
    """May Payment collect, and how much? Collect only when status is
    "pending"; "completed" means already paid.
    Returns ({"id", "amount_cents", "status"}, 200), or
    ({"error": ...}, 404), or 409 when the hold has expired."""
    cur.execute(EXPIRE_HOLDS)
    cur.execute(
        "SELECT id, amount_cents, status FROM bookings "
        "WHERE id = %s AND status <> 'cancelled'",
        (booking_id,),
    )
    facts = cur.fetchone()
    if facts is None:
        return {"error": "booking not found"}, 404
    if facts["status"] == "expired":
        return {"error": "booking hold has expired"}, 409
    return facts, 200


def mark_paid(cur, booking_id, card_last4) -> dict | None:
    """Returns the stored row: paid = confirmed (also a repeat or a late
    success on a free slot); unpaid = refused. None if missing or cancelled."""
    # The status guard is the race fix: of two concurrent callers, the second
    # waits for the row lock, then matches nothing.
    try:
        with cur.connection.transaction():  # a refusal must not abort the request
            cur.execute(
                "UPDATE bookings SET status = 'completed', card_last4 = %s "
                "WHERE id = %s AND status IN ('pending', 'expired')",
                (card_last4, booking_id),
            )
    except (DeadlockDetected, ExclusionViolation):
        pass  # expired and re-booked: the constraint keeps the newer booking
    cur.execute(
        f"SELECT {BOOKING_COLUMNS} FROM bookings "
        "WHERE id = %s AND status <> 'cancelled'",
        (booking_id,),
    )
    return cur.fetchone()


def cancel_booking(cur, booking_id) -> dict | None:
    """Soft cancel: the row as it was, or None if unknown or cancelled."""
    with cur.connection.transaction():
        # FOR UPDATE waits for a payment in flight, so the answer shows it.
        cur.execute(
            f"SELECT {BOOKING_COLUMNS} FROM bookings "
            "WHERE id = %s AND status <> 'cancelled' FOR UPDATE",
            (booking_id,),
        )
        row = cur.fetchone()
        if row is not None:
            cur.execute(
                "UPDATE bookings SET status = 'cancelled' WHERE id = %s", (booking_id,)
            )
        return row
