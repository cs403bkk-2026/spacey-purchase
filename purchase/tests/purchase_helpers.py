import os
from datetime import UTC, datetime, timedelta

import purchase.booking
from app import create_app

NOW = datetime.now(UTC)


def make_client(reset=True):
    client = create_app().test_client()
    client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {os.environ['PURCHASE_TOKEN']}"
    if reset:  # with the test defaults: empty tables, then space 1 "Founders Desk"
        assert client.post("/test/reset").status_code == 204
    return client


def slot(start_hours, end_hours):
    """Booking times relative to now, e.g. slot(-1, 1) is happening right now."""
    return {
        "start_time": (NOW + timedelta(hours=start_hours)).isoformat(),
        "end_time": (NOW + timedelta(hours=end_hours)).isoformat(),
    }


def register(client, email="annabel@example.com", password="hunter22"):
    return client.post("/register", json={"email": email, "password": password})


def login(client, email="annabel@example.com", password="hunter22"):
    return client.post(
        "/users/authenticate", json={"email": email, "password": password}
    )


def pay(client, booking_id, card_last4="4242"):
    """Record a payment as Payment's /pay handler will: through mark_paid."""
    with client.application.db.cursor() as cur:
        row = purchase.booking.mark_paid(cur, booking_id, card_last4)
    return purchase.booking.booking_to_json(row)
