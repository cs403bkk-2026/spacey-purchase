"""Space helpers: validating a space and finding which are booked."""


def is_valid_capacity(capacity) -> bool:
    # bool is a subclass of int in Python, so rule out true/false
    return (
        isinstance(capacity, int)
        and not isinstance(capacity, bool)
        and capacity >= 1
    )


def is_valid_name(name) -> bool:
    return isinstance(name, str) and name.strip() != ""


def is_valid_price(price) -> bool:
    return isinstance(price, int) and not isinstance(price, bool) and price >= 0


def booked_space_ids(cur, window) -> set:
    """Spaces booked during the window, or booked right now if no window."""
    if window is None:
        cur.execute(
            "SELECT DISTINCT space_id FROM bookings "
            "WHERE start_time <= now() AND end_time > now()"
        )
    else:
        start_time, end_time = window
        # Same overlap rule as booking creation: back-to-back is not a clash.
        cur.execute(
            "SELECT DISTINCT space_id FROM bookings "
            "WHERE start_time < %s AND end_time > %s",
            (end_time, start_time),
        )
    return {row["space_id"] for row in cur.fetchall()}
