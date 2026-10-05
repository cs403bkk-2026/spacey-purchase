"""Booking helpers: pricing, booking a space and finding bookings."""

from datetime import datetime

from psycopg.errors import DeadlockDetected, ExclusionViolation

from purchase.member import is_subscribed

BOOKING_COLUMNS = (
    "id, space_id, member, paid, start_time, end_time, "
    "amount_cents, user_id, card_last4, created_at"
)


def calculate_booking_price_cents(
    hourly_rate_cents: int, start_time: datetime, end_time: datetime
) -> int:
    """Prorate an hourly rate, rounding half cents up.

    The caller supplies a validated interval and handles subscription coverage.
    Duration is truncated to whole seconds, preserving the existing calculation.
    """
    seconds = int((end_time - start_time).total_seconds())
    return (hourly_rate_cents * seconds + 1800) // 3600


def create_booking(cur, space_id, member, start_time, end_time, party_size, user_id):
    """Book a space for a member; user_id is the logged-in account or None.
    Returns (payload, status) - the raw booking row, or an {"error": ...}."""
    cur.execute(
        "SELECT id, capacity, price_cents FROM spaces WHERE id = %s",
        (space_id,),
    )
    space = cur.fetchone()
    if space is None:
        return {"error": "space not found"}, 404

    if end_time <= start_time:
        return {"error": "end_time must be after start_time"}, 400
    # bool is a subclass of int in Python, so rule out true/false
    if (
        not isinstance(party_size, int)
        or isinstance(party_size, bool)
        or party_size < 1
    ):
        return {
            "error": "party_size must be a whole number of at least 1"
        }, 400
    if party_size > space["capacity"]:
        return {
            "error": f"party_size {party_size} exceeds this space's "
            f"capacity of {space['capacity']}"
        }, 400

    # Overlap = starts before the other ends AND ends after the
    # other starts. Back-to-back bookings (10-11, 11-12) are allowed.
    cur.execute(
        "SELECT id FROM bookings "
        "WHERE space_id = %s AND start_time < %s AND end_time > %s",
        (space_id, end_time, start_time),
    )
    if cur.fetchone() is not None:
        return {"error": "space is already booked for that time"}, 409

    # Unpaid until POST /bookings/<id>/pay is called - unless the
    # member subscribes: then it is paid at once and costs nothing extra.
    subscribed = is_subscribed(cur, member)
    try:
        cur.execute(
            "INSERT INTO bookings "
            "(space_id, member, paid, start_time, end_time, "
            "amount_cents, user_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) "
            f"RETURNING {BOOKING_COLUMNS}",
            (
                space_id,
                member,
                subscribed,
                start_time,
                end_time,
                0
                if subscribed
                else calculate_booking_price_cents(
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
    cur.execute(f"SELECT {BOOKING_COLUMNS} FROM bookings ORDER BY start_time")
    return cur.fetchall()


def list_space_bookings(cur, space_id) -> list:
    cur.execute(
        f"SELECT {BOOKING_COLUMNS} FROM bookings "
        "WHERE space_id = %s ORDER BY start_time",
        (space_id,),
    )
    return cur.fetchall()


def get_booking(cur, booking_id) -> dict | None:
    cur.execute(
        f"SELECT {BOOKING_COLUMNS} FROM bookings WHERE id = %s", (booking_id,)
    )
    return cur.fetchone()


def cancel_booking(cur, booking_id) -> dict | None:
    """Delete a booking (its access code goes with it); None if not found."""
    cur.execute(
        f"DELETE FROM bookings WHERE id = %s RETURNING {BOOKING_COLUMNS}",
        (booking_id,),
    )
    return cur.fetchone()


