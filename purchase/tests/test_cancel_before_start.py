from datetime import UTC, datetime

import pytest
from purchase_helpers import make_client, pay, slot

import purchase.booking
import purchase.space

STARTED = {"error": "booking has already started"}
NOT_FOUND = {"error": "booking not found"}


def book(client, start_hours=1, end_hours=2):
    response = client.post(
        "/spaces/1/bookings",
        json={"member": "annabel", **slot(start_hours, end_hours)},
    )
    assert response.status_code == 201
    return response.get_json()["id"]


def stored_row(client, booking_id):
    with client.application.db.cursor() as cur:
        cur.execute("SELECT * FROM bookings WHERE id = %s", (booking_id,))
        return cur.fetchone()


def move_start(client, booking_id, offset):
    with client.application.db.cursor() as cur:
        cur.execute(
            "UPDATE bookings SET start_time = now() + %s::interval WHERE id = %s",
            (offset, booking_id),
        )


def leave_pending(client, booking_id):
    pass


def pay_for_it(client, booking_id):
    pay(client, booking_id)


def let_the_hold_expire(client, booking_id):
    with client.application.db.cursor() as cur:
        cur.execute(
            "UPDATE bookings SET status = 'expired' WHERE id = %s", (booking_id,)
        )


@pytest.mark.parametrize(
    "make_state",
    [leave_pending, pay_for_it, let_the_hold_expire],
    ids=["pending", "paid", "expired"],
)
def test_cancel_after_the_start_is_409(make_state):
    client = make_client()
    booking_id = book(client, -1, 1)
    make_state(client, booking_id)
    before = stored_row(client, booking_id)

    response = client.delete(f"/bookings/{booking_id}")

    assert response.status_code == 409
    assert response.get_json() == STARTED
    assert stored_row(client, booking_id) == before


@pytest.mark.parametrize(
    ("offset", "refusal", "cancels"),
    [("0", (STARTED, 409), False), ("1 microsecond", None, True)],
    ids=["at_start", "a_microsecond_before"],
)
def test_the_start_instant_is_too_late(cur, offset, refusal, cancels):
    space, _ = purchase.space.create_space(cur, "Test Room", 4, 2500)
    booking, _ = purchase.booking.create_booking(
        cur,
        space["id"],
        "annabel",
        datetime(2030, 1, 1, 10, tzinfo=UTC),
        datetime(2030, 1, 1, 11, tzinfo=UTC),
        1,
        None,
    )
    # One transaction, so now() is the same instant in every statement.
    cur.execute(
        "UPDATE bookings SET start_time = now() + %s::interval WHERE id = %s",
        (offset, booking["id"]),
    )

    answer = purchase.booking.check_cancellable(cur, booking["id"])
    row = purchase.booking.cancel_booking(cur, booking["id"])

    assert answer == refusal
    assert (row is not None and row["id"] == booking["id"]) is cancels


def test_delete_checks_the_start_again():
    client = make_client()
    booking_id = book(client)
    check = client.get(f"/bookings/{booking_id}/cancellable")
    move_start(client, booking_id, "-1 minute")
    before = stored_row(client, booking_id)

    response = client.delete(f"/bookings/{booking_id}")

    assert check.status_code == 204
    assert response.status_code == 409
    assert response.get_json() == STARTED
    assert stored_row(client, booking_id) == before


def an_unknown_booking(client):
    return 999


def a_cancelled_booking(client):
    booking_id = book(client)
    client.delete(f"/bookings/{booking_id}")
    return booking_id


def a_pending_booking(client):
    return book(client)


def a_paid_booking(client):
    booking_id = book(client)
    pay(client, booking_id)
    return booking_id


def a_started_booking(client):
    return book(client, -1, 1)


@pytest.mark.parametrize(
    ("make_booking", "status", "body"),
    [
        (an_unknown_booking, 404, NOT_FOUND),
        (a_cancelled_booking, 404, NOT_FOUND),
        (a_pending_booking, 204, None),
        (a_paid_booking, 204, None),
        (a_started_booking, 409, STARTED),
    ],
    ids=["unknown", "cancelled", "pending", "paid", "started"],
)
def test_check_cancellable_answers(make_booking, status, body):
    client = make_client()
    booking_id = make_booking(client)

    response = client.get(f"/bookings/{booking_id}/cancellable")

    assert response.status_code == status
    assert response.get_json(silent=True) == body
    assert (response.data == b"") is (status == 204)


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer wrong"}], ids=["missing", "wrong"]
)
def test_cancellable_needs_the_token(headers):
    client = make_client()
    booking_id = book(client)
    without_token = client.application.test_client()

    response = without_token.get(f"/bookings/{booking_id}/cancellable", headers=headers)

    assert response.status_code == 403
    assert response.get_json() == {"error": "invalid service token"}
