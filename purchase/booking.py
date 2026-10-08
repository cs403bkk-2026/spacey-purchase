"""Booking helpers: pricing, booking a space and finding bookings."""

from datetime import datetime, timezone

from psycopg import Error as DatabaseError
from psycopg.errors import DeadlockDetected, ExclusionViolation

from shared.logger import logger
from payment.services import authorize_card
from purchase.member import normalise_member_name, is_subscribed

BOOKING_COLUMNS = (
    "id, space_id, member, paid, start_time, end_time, "
    "amount_cents, user_id, card_last4, created_at"
)


def booking_to_json(row: dict) -> dict:
    """A booking row ready for jsonify: its times as UTC ISO 8601 strings."""
    return {
        **row,
        "start_time": row["start_time"].astimezone(timezone.utc).isoformat(),
        "end_time": row["end_time"].astimezone(timezone.utc).isoformat(),
        "created_at": row["created_at"].astimezone(timezone.utc).isoformat(),
    }


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


def list_user_bookings(cur, user_id) -> list:
    """Bookings made while logged in as user_id; guest bookings never match."""
    cur.execute(
        f"SELECT {BOOKING_COLUMNS} FROM bookings "
        "WHERE user_id = %s ORDER BY start_time",
        (user_id,),
    )
    return cur.fetchall()


def get_booking(cur, booking_id) -> dict | None:
    cur.execute(
        f"SELECT {BOOKING_COLUMNS} FROM bookings WHERE id = %s", (booking_id,)
    )
    return cur.fetchone()


def mark_paid(cur, booking_id, card_last4) -> dict | None:
    """Flip the booking to paid, keeping only the card's last 4 digits.
    Returns the updated row."""
    cur.execute(
        "UPDATE bookings SET paid = TRUE, card_last4 = %s WHERE id = %s "
        f"RETURNING {BOOKING_COLUMNS}",
        (card_last4, booking_id),
    )
    return cur.fetchone()


def mark_booking_paid(cur, booking_id, card_number, expiry, cvc, force_failure=False):
    """Pay for a booking. Payment decides whether the card is accepted
    (payment.services.authorize_card); this owns the booking's paid state.
    Paying an already-paid booking is a no-op rather than an error, so a
    retried request can't break the flow or charge twice - and doesn't need
    a card either. Only the card's last 4 digits are ever stored.
    Returns (payload, status) - the raw booking row, or an {"error": ...}."""
    logger.debug("payment booking_id=%s outcome=started", booking_id)
    try:
        row = get_booking(cur, booking_id)
        if row is None:
            logger.warning("payment booking_id=%s outcome=not_found", booking_id)
            return {"error": "booking not found"}, 404

        if row["paid"]:
            logger.info("payment booking_id=%s outcome=already_paid", booking_id)
            return row, 200

        rejected = authorize_card(card_number, expiry, cvc, force_failure)
        if rejected:
            outcome = "invalid_card" if rejected[1] == 400 else "failed"
            logger.warning("payment booking_id=%s outcome=%s", booking_id, outcome)
            return rejected

        row = mark_paid(cur, booking_id, card_number[-4:])
        logger.info("payment booking_id=%s outcome=succeeded", booking_id)
        return row, 200
    except DatabaseError:
        # Database diagnostics may include SQL parameters; never log the exception.
        logger.error("payment booking_id=%s outcome=database_error", booking_id)
        return {"error": "payment unavailable"}, 500


def cancel_booking(cur, booking_id) -> dict | None:
    """Delete a booking (its access code goes with it); None if not found."""
    cur.execute(
        f"DELETE FROM bookings WHERE id = %s RETURNING {BOOKING_COLUMNS}",
        (booking_id,),
    )
    return cur.fetchone()


