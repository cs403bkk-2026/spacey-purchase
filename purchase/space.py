"""Space helpers: validating, storing and finding which spaces are booked."""


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


SPACE_COLUMNS = "id, name, capacity, price_cents"


def list_spaces(cur) -> list:
    cur.execute(f"SELECT {SPACE_COLUMNS} FROM spaces")
    return cur.fetchall()


def get_space(cur, space_id) -> dict | None:
    cur.execute(f"SELECT {SPACE_COLUMNS} FROM spaces WHERE id = %s", (space_id,))
    return cur.fetchone()


def create_space(cur, name, capacity, price_cents):
    """Add a space after checking its fields; the name is stored trimmed.
    Returns (payload, status) - the new space, or an {"error": ...}."""
    if name is None or capacity is None:
        return {"error": "name and capacity are required"}, 400
    if not is_valid_name(name):
        return {"error": "name must not be empty"}, 400
    if not is_valid_capacity(capacity):
        return {"error": "capacity must be a whole number of at least 1"}, 400
    if not is_valid_price(price_cents):
        return {"error": "price_cents must be a non-negative integer"}, 400

    cur.execute(
        "INSERT INTO spaces (name, capacity, price_cents) VALUES (%s, %s, %s) "
        f"RETURNING {SPACE_COLUMNS}",
        (name.strip(), capacity, price_cents),
    )
    return cur.fetchone(), 201


def update_space(cur, space_id, fields):
    """Change a space's name, capacity and/or price. fields is the request
    body: a field left out keeps its current value, but one sent as null is
    rejected - so this needs the body itself, not just its values.
    Returns (payload, status) - the updated space, or an {"error": ...}."""
    if not any(field in fields for field in ("name", "capacity", "price_cents")):
        return {"error": "provide name, capacity and/or price_cents to update"}, 400
    if get_space(cur, space_id) is None:
        return {"error": "space not found"}, 404

    name = fields.get("name")
    capacity = fields.get("capacity")
    price_cents = fields.get("price_cents")
    if "name" in fields and not is_valid_name(name):
        return {"error": "name must not be empty"}, 400
    if "capacity" in fields and not is_valid_capacity(capacity):
        return {"error": "capacity must be a whole number of at least 1"}, 400
    if "price_cents" in fields and not is_valid_price(price_cents):
        return {"error": "price_cents must be a non-negative integer"}, 400
    if name is not None:
        name = name.strip()

    # COALESCE keeps the current value for fields not in the request
    cur.execute(
        "UPDATE spaces "
        "SET name = COALESCE(%s, name), "
        "capacity = COALESCE(%s, capacity), "
        "price_cents = COALESCE(%s, price_cents) "
        "WHERE id = %s "
        f"RETURNING {SPACE_COLUMNS}",
        (name, capacity, price_cents, space_id),
    )
    return cur.fetchone(), 200


def delete_space(cur, space_id):
    """Delete a space, refused while it still has bookings.
    Returns (payload, status) - payload is None once deleted."""
    if get_space(cur, space_id) is None:
        return {"error": "space not found"}, 404
    cur.execute(
        "SELECT 1 FROM bookings WHERE space_id = %s LIMIT 1", (space_id,)
    )
    if cur.fetchone() is not None:
        return {"error": "space has bookings, cancel them first"}, 409
    cur.execute("DELETE FROM spaces WHERE id = %s", (space_id,))
    return None, 204


def seed_starter_space(cur) -> None:
    """Give an empty database one space, so there is something to book."""
    cur.execute("SELECT COUNT(*) AS count FROM spaces")
    if cur.fetchone()["count"] == 0:
        cur.execute(
            "INSERT INTO spaces (name, capacity, price_cents) "
            "VALUES (%s, %s, %s)",
            ("Founders Desk", 1, 2500),  # $25.00 per hour
        )
