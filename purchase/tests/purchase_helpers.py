from datetime import date, datetime, timedelta, timezone

from app import create_app

NOW = datetime.now(timezone.utc)


def make_client():
    return create_app(reset_on_start=True).test_client()


def slot(start_hours, end_hours):
    """Booking times relative to now, e.g. slot(-1, 1) is happening right now."""
    return {
        "start_time": (NOW + timedelta(hours=start_hours)).isoformat(),
        "end_time": (NOW + timedelta(hours=end_hours)).isoformat(),
    }


VALID_CARD = {
    "card_number": "4242424242424242",
    "expiry": f"12/{(date.today().year + 1) % 100:02d}",
    "cvc": "123",
}


def register(client, email="annabel@example.com", password="hunter22"):
    return client.post("/register", json={"email": email, "password": password})


def login(client, email="annabel@example.com", password="hunter22"):
    return client.post("/login", json={"email": email, "password": password})


def pay(client, booking_id):
    return client.post(f"/bookings/{booking_id}/pay", json=VALID_CARD)
