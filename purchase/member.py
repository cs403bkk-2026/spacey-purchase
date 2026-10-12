"""Member helpers: accounts, and the member name a booking is stored under."""

import re

from psycopg.errors import UniqueViolation
from werkzeug.security import check_password_hash, generate_password_hash

# Deliberately simple: good enough to catch a typo, not full RFC 5322.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email(email) -> bool:
    return isinstance(email, str) and EMAIL_RE.match(email.strip()) is not None


def is_valid_password(password) -> bool:
    return isinstance(password, str) and len(password) >= 8


def register_user(cur, email, password):
    """Create an account, storing only a hash of the password.
    Returns (payload, status) - {"id": ..., "email": ...}, or an error."""
    if not is_valid_email(email):
        return {"error": "enter a valid email address"}, 400
    if not is_valid_password(password):
        return {"error": "password must be at least 8 characters"}, 400

    email = email.strip().lower()
    password_hash = generate_password_hash(password)

    try:
        cur.execute(
            "INSERT INTO users (email, password_hash) VALUES (%s, %s) "
            "RETURNING id, email",
            (email, password_hash),
        )
    except UniqueViolation:
        return {"error": "email is already registered"}, 409
    return cur.fetchone(), 201


def authenticate(cur, email, password):
    """Check an email and password; the caller starts the session.
    Returns (payload, status) - {"id": ..., "email": ...}, or an error.
    Wrong password and unknown email give the identical error, so a
    failed attempt can't be used to find out which emails are registered."""
    invalid = {"error": "invalid email or password"}, 401
    if not isinstance(email, str) or not isinstance(password, str):
        return invalid

    cur.execute(
        "SELECT id, email, password_hash FROM users WHERE email = %s",
        (email.strip().lower(),),
    )
    user = cur.fetchone()

    if user is None or not check_password_hash(user["password_hash"], password):
        return invalid
    return {"id": user["id"], "email": user["email"]}, 200


def normalise_member_name(name) -> str | None:
    """The name a booking is stored under: trimmed, "guest" if missing or
    blank. None if it isn't a string at all."""
    if name is None:
        return "guest"
    if not isinstance(name, str):
        return None
    return name.strip() or "guest"


def subscribe(cur, name):
    """Subscriptions are removed: always 410, touches no data, so an old
    client gets a readable error instead of a 404."""
    return {"error": "subscriptions have been removed"}, 410
