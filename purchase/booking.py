"""Booking helpers: parsing times, pricing and booking a space."""

from datetime import datetime, timezone

from psycopg.errors import DeadlockDetected, ExclusionViolation

from purchase.member import is_subscribed


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
            "RETURNING id, space_id, member, paid, start_time, end_time, "
            "amount_cents, user_id, card_last4, created_at",
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


def parse_time(value) -> datetime | None:
    """ISO 8601 with a timezone, e.g. 2026-09-25T09:00:00+07:00."""
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def parse_window(args) -> tuple[tuple | None, str | None]:
    """Optional ?start_time=&end_time= as (window, error); window is None if absent."""
    raw_start, raw_end = args.get("start_time"), args.get("end_time")
    if raw_start is None and raw_end is None:
        return None, None
    start_time, end_time = parse_time(raw_start), parse_time(raw_end)
    if start_time is None or end_time is None:
        return None, (
            "start_time and end_time must be given together "
            "(ISO 8601 with timezone, e.g. 2026-09-25T09:00:00Z)"
        )
    if end_time <= start_time:
        return None, "end_time must be after start_time"
    return (start_time, end_time), None


def booking_to_json(row: dict) -> dict:
    return {
        **row,
        "start_time": row["start_time"].astimezone(timezone.utc).isoformat(),
        "end_time": row["end_time"].astimezone(timezone.utc).isoformat(),
        "created_at": row["created_at"].astimezone(timezone.utc).isoformat(),
    }
