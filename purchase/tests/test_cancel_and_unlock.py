import os
import threading
import time

import psycopg
import pytest
from psycopg.rows import dict_row
from purchase_helpers import login, make_client, pay, register, slot

import purchase.booking


def book(client, space_id=1, start_hours=1, end_hours=2, **body):
    response = client.post(
        f"/spaces/{space_id}/bookings",
        json={"member": "annabel", **slot(start_hours, end_hours), **body},
    )
    assert response.status_code == 201
    return response.get_json()


def cancel(client, booking_id):
    return client.delete(f"/bookings/{booking_id}")


def stored_rows(client):
    """Every bookings row as stored, cancelled ones included."""
    with client.application.db.cursor() as cur:
        cur.execute("SELECT * FROM bookings ORDER BY id")
        return cur.fetchall()


def stored_status(client, booking_id):
    with client.application.db.cursor() as cur:
        cur.execute("SELECT status FROM bookings WHERE id = %s", (booking_id,))
        return cur.fetchone()["status"]


def wait_until_waiting_on_a_lock(backend_pid):
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as watcher:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            row = watcher.execute(
                "SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s",
                (backend_pid,),
            ).fetchone()
            if row is not None and row[0] == "Lock":
                return
            time.sleep(0.05)
    pytest.fail("the cancel never waited for the payment's lock")


@pytest.mark.parametrize("paid", [False, True], ids=["pending", "paid"])
def test_cancel_returns_the_booking_as_it_was(paid):
    client = make_client()
    booking = book(client)
    if paid:
        pay(client, booking["id"])
    before = client.get(f"/bookings/{booking['id']}").get_json()

    response = cancel(client, booking["id"])

    assert response.status_code == 200
    assert response.get_json() == before
    assert stored_status(client, booking["id"]) == "cancelled"


def test_cancelling_again_is_404():
    client = make_client()
    booking = book(client)
    cancel(client, booking["id"])
    rows = stored_rows(client)

    response = cancel(client, booking["id"])

    assert response.status_code == 404
    assert response.get_json() == {"error": "booking not found"}
    assert stored_rows(client) == rows


def test_cancel_waits_for_a_payment_in_flight():
    client = make_client()
    booking = book(client)
    answers = []
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as payer:
        purchase.booking.mark_paid(payer.cursor(), booking["id"], "4242")
        thread = threading.Thread(
            target=lambda: answers.append(cancel(client, booking["id"]))
        )
        thread.start()
        wait_until_waiting_on_a_lock(client.application.db.info.backend_pid)
        payer.commit()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert answers[0].status_code == 200
    assert answers[0].get_json()["paid"] is True
    assert answers[0].get_json()["card_last4"] == "4242"


def test_a_cancelled_booking_reads_as_missing():
    client = make_client()
    register(client)
    user = login(client).get_json()
    booking = book(client, user_id=user["id"])
    cancel(client, booking["id"])

    assert client.get(f"/bookings/{booking['id']}").status_code == 404
    for path in ("/bookings", "/spaces/1/bookings", f"/users/{user['id']}/bookings"):
        assert client.get(path).get_json()["bookings"] == [], path


def test_a_cancelled_booking_frees_its_slot():
    client = make_client()
    booking = book(client, start_hours=-1, end_hours=1)
    cancel(client, booking["id"])

    now = client.get("/spaces/booked-ids").get_json()
    window = client.get("/spaces/booked-ids", query_string=slot(-1, 1)).get_json()

    assert now == {"space_ids": []}
    assert window == {"space_ids": []}
    book(client, start_hours=-1, end_hours=1)


def test_delete_space_takes_its_cancelled_bookings():
    client = make_client()
    client.post("/spaces", json={"name": "Meeting Room A", "capacity": 6})
    paid = book(client, space_id=2)
    pay(client, paid["id"])
    pending = book(client, space_id=2, start_hours=3, end_hours=4)
    cancel(client, paid["id"])
    cancel(client, pending["id"])

    response = client.delete("/spaces/2")

    assert response.status_code == 204
    assert [row for row in stored_rows(client) if row["space_id"] == 2] == []
    assert cancel(client, paid["id"]).status_code == 404


@pytest.mark.parametrize("paid", [False, True], ids=["pending", "paid"])
def test_delete_space_with_an_uncancelled_booking_is_409(paid):
    client = make_client()
    client.post("/spaces", json={"name": "Meeting Room A", "capacity": 6})
    cancelled = book(client, space_id=2)
    cancel(client, cancelled["id"])
    kept = book(client, space_id=2, start_hours=3, end_hours=4)
    if paid:
        pay(client, kept["id"])
    rows = stored_rows(client)

    response = client.delete("/spaces/2")

    assert response.status_code == 409
    assert response.get_json() == {"error": "space has bookings, cancel them first"}
    assert stored_rows(client) == rows
    assert client.get("/spaces/2").status_code == 200


REFUSALS = {
    402: {"error": "booking is not paid"},
    404: {"error": "booking not found"},
}


def unknown(client):
    return 999


def pending(client):
    return book(client)["id"]


def paid(client):
    booking_id = book(client)["id"]
    pay(client, booking_id)
    return booking_id


def cancelled_after_paid(client):
    booking_id = paid(client)
    cancel(client, booking_id)
    return booking_id


def completed_at_zero(client):
    """Like a copied booking a subscription covered: paid at 0, no card."""
    with client.application.db.cursor() as cur:
        cur.execute(
            "INSERT INTO bookings "
            "(space_id, member, start_time, end_time, amount_cents, status) "
            "VALUES (1, 'annabel', now() + interval '1 hour', "
            "now() + interval '2 hours', 0, 'completed') RETURNING id"
        )
        return cur.fetchone()["id"]


@pytest.mark.parametrize(
    ("make_booking", "status"),
    [
        (unknown, 404),
        (pending, 402),
        (cancelled_after_paid, 404),
        (paid, 204),
        (completed_at_zero, 204),
    ],
    ids=["unknown", "pending", "cancelled_after_paid", "paid", "completed_at_zero"],
)
def test_check_unlockable_answers(make_booking, status):
    client = make_client()
    booking_id = make_booking(client)

    response = client.get(f"/bookings/{booking_id}/unlockable")

    assert response.status_code == status
    if status == 204:
        assert response.data == b""
    else:
        assert response.get_json() == REFUSALS[status]


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer wrong"}], ids=["missing", "wrong"]
)
def test_unlockable_needs_the_token(headers):
    client = make_client()
    booking_id = paid(client)
    without_token = client.application.test_client()

    response = without_token.get(f"/bookings/{booking_id}/unlockable", headers=headers)

    assert response.status_code == 403
    assert response.get_json() == {"error": "invalid service token"}


def test_hub_journey_pay_unlock_cancel_retry():
    client = make_client()
    body = {"member": "annabel", **slot(1, 2)}

    booked = client.post("/spaces/1/bookings", json=body)
    assert booked.status_code == 201
    booking_id = booked.get_json()["id"]
    assert pay(client, booking_id)["paid"] is True

    assert client.get(f"/bookings/{booking_id}/unlockable").status_code == 204

    cancelled = cancel(client, booking_id)
    assert cancelled.status_code == 200
    assert cancelled.get_json()["paid"] is True
    assert cancel(client, booking_id).status_code == 404
    assert client.get(f"/bookings/{booking_id}/unlockable").status_code == 404

    assert client.post("/spaces/1/bookings", json=body).status_code == 201
