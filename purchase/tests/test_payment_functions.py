import os
import threading
import time

import psycopg
import pytest
from psycopg.rows import dict_row
from purchase_helpers import BOOKING_KEYS, make_client, slot

import purchase.booking


def book(client, space_id=1):
    """An unpaid one-hour booking for annabel; returns its id."""
    response = client.post(
        f"/spaces/{space_id}/bookings", json={"member": "annabel", **slot(1, 2)}
    )
    return response.get_json()["id"]


def pay(client, booking_id, card_last4="4242"):
    """What the hub sends after Payment answers success."""
    return client.post(f"/bookings/{booking_id}/paid", json={"card_last4": card_last4})


def payable(client, booking_id):
    return client.get(f"/bookings/{booking_id}/payable")


def stored_row(client, booking_id):
    with client.application.db.cursor() as cur:
        cur.execute("SELECT * FROM bookings WHERE id = %s", (booking_id,))
        return cur.fetchone()


def cancel_with_sql(client, booking_id):
    with client.application.db.cursor() as cur:
        cur.execute(
            "UPDATE bookings SET status = 'cancelled' WHERE id = %s", (booking_id,)
        )


def a_paid_booking(client):
    booking_id = book(client)
    pay(client, booking_id)
    return booking_id, 2500


def an_old_booking_paid_at_zero(client):
    with client.application.db.cursor() as cur:
        cur.execute(
            "INSERT INTO bookings "
            "(space_id, member, status, start_time, end_time, amount_cents) "
            "VALUES (1, 'annabel', 'completed', now() + interval '1 hour', "
            "now() + interval '2 hours', 0) RETURNING id"
        )
        return cur.fetchone()["id"], 0


def an_unknown_id(client):
    return 999


def a_cancelled_booking(client):
    booking_id = book(client)
    cancel_with_sql(client, booking_id)
    return booking_id


def test_payable_pending_booking_gives_its_recorded_amount():
    client = make_client()
    space_id = client.post(
        "/spaces", json={"name": "Meeting Room A", "capacity": 6, "price_cents": 1500}
    ).get_json()["id"]
    booking_id = book(client, space_id)
    client.patch(f"/spaces/{space_id}", json={"price_cents": 2000})
    before = stored_row(client, booking_id)

    response = payable(client, booking_id)

    assert response.status_code == 200
    assert response.get_json() == {
        "id": booking_id,
        "amount_cents": 1500,
        "status": "pending",
    }
    assert stored_row(client, booking_id) == before


@pytest.mark.parametrize(
    "make_booking",
    [a_paid_booking, an_old_booking_paid_at_zero],
    ids=["paid", "old_paid_at_zero"],
)
def test_payable_already_paid_booking_is_completed(make_booking):
    client = make_client()
    booking_id, amount_cents = make_booking(client)

    response = payable(client, booking_id)

    assert response.status_code == 200
    assert response.get_json() == {
        "id": booking_id,
        "amount_cents": amount_cents,
        "status": "completed",
    }


@pytest.mark.parametrize(
    "missing_booking",
    [an_unknown_id, a_cancelled_booking],
    ids=["unknown_id", "cancelled"],
)
def test_payable_missing_booking_is_404(missing_booking):
    client = make_client()
    booking_id = missing_booking(client)

    response = payable(client, booking_id)

    assert response.status_code == 404
    assert response.get_json() == {"error": "booking not found"}


def test_paid_success_completes_the_booking():
    client = make_client()
    booking_id = book(client)

    response = pay(client, booking_id)

    body = response.get_json()
    assert response.status_code == 200
    assert body.keys() == BOOKING_KEYS
    assert body["paid"] is True
    assert body["card_last4"] == "4242"
    assert client.get(f"/bookings/{booking_id}").get_json() == body


def test_paid_repeated_success_returns_the_stored_row():
    client = make_client()
    booking_id = book(client)
    first = pay(client, booking_id, "4242").get_json()

    response = pay(client, booking_id, "1111")

    assert response.status_code == 200
    assert response.get_json() == first
    assert stored_row(client, booking_id)["card_last4"] == "4242"


def test_paid_two_concurrent_successes_change_the_row_once():
    client = make_client()
    booking_id = book(client)
    url = os.environ["DATABASE_URL"]
    answers = {}

    with psycopg.connect(url) as holder:
        holder.execute(
            "SELECT id FROM bookings WHERE id = %s FOR UPDATE", (booking_id,)
        )
        payers = {
            card: psycopg.connect(url, autocommit=True, row_factory=dict_row)
            for card in ("4242", "1111")
        }
        pids = [conn.info.backend_pid for conn in payers.values()]
        threads = [
            threading.Thread(
                target=lambda card=card, conn=conn: answers.__setitem__(
                    card, purchase.booking.mark_paid(conn.cursor(), booking_id, card)
                )
            )
            for card, conn in payers.items()
        ]
        for thread in threads:
            thread.start()
        # Release the lock only once both payers wait on it.
        for _ in range(100):
            waiting = holder.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE pid = ANY(%s) AND wait_event_type = 'Lock'",
                (pids,),
            ).fetchone()[0]
            if waiting == 2:
                break
            time.sleep(0.05)
        else:
            pytest.fail("the two payers did not both wait on the row lock")
        assert answers == {}
        holder.commit()
    for thread in threads:
        thread.join(timeout=10)
    for conn in payers.values():
        conn.close()

    shown = client.get(f"/bookings/{booking_id}").get_json()
    assert shown["paid"] is True
    assert answers["4242"] == answers["1111"]
    assert purchase.booking.booking_to_json(answers["4242"]) == shown


@pytest.mark.parametrize("payment_answer", ["failed", "unknown"])
def test_failed_or_unknown_payment_leaves_the_booking_payable(payment_answer):
    client = make_client()
    booking_id = book(client)
    before = payable(client, booking_id).get_json()
    # After a failed or unknown answer the hub sends nothing to Purchase.

    again = payable(client, booking_id)
    later_success = pay(client, booking_id)

    assert again.status_code == 200
    assert again.get_json() == before
    assert before == {"id": booking_id, "amount_cents": 2500, "status": "pending"}
    assert later_success.status_code == 200
    assert later_success.get_json()["paid"] is True


@pytest.mark.parametrize(
    "paid_first", [False, True], ids=["was_pending", "was_completed"]
)
def test_paid_after_cancel_is_404_and_changes_nothing(paid_first):
    client = make_client()
    booking_id = book(client)
    if paid_first:
        pay(client, booking_id, "4242")
    cancel_with_sql(client, booking_id)
    before = stored_row(client, booking_id)

    response = pay(client, booking_id, "1111")

    assert response.status_code == 404
    assert response.get_json() == {"error": "booking not found"}
    assert stored_row(client, booking_id) == before


def test_paid_unknown_booking_is_404():
    client = make_client()

    response = pay(client, 999)

    assert response.status_code == 404
    assert response.get_json() == {"error": "booking not found"}
    assert stored_row(client, 999) is None


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"card_last4": "424"},
        {"card_last4": "42424"},
        {"card_last4": 4242},
        {"card_last4": "\uff14\uff12\uff14\uff12"},
        {"card_last4": "4242", "card_number": "4242424242424242"},
        ["4242"],
    ],
    ids=[
        "missing",
        "three_digits",
        "five_digits",
        "number",
        "fullwidth_digits",
        "extra_card_number",
        "not_an_object",
    ],
)
def test_paid_rejects_any_other_body(body):
    client = make_client()
    booking_id = book(client)
    before = stored_row(client, booking_id)

    response = client.post(f"/bookings/{booking_id}/paid", json=body)

    assert response.status_code == 400
    assert response.get_json() == {"error": "send only card_last4, as 4 digits"}
    assert stored_row(client, booking_id) == before


@pytest.mark.parametrize(
    ("method", "action", "body"),
    [("GET", "payable", None), ("POST", "paid", {"card_last4": "4242"})],
    ids=["payable", "paid"],
)
def test_payment_endpoints_need_the_token(method, action, body):
    client = make_client()
    booking_id = book(client)
    before = stored_row(client, booking_id)
    without_token = client.application.test_client()

    responses = [
        without_token.open(
            f"/bookings/{booking_id}/{action}",
            method=method,
            json=body,
            headers=headers,
        )
        for headers in ({}, {"Authorization": "Bearer wrong"})
    ]

    for response in responses:
        assert response.status_code == 403
        assert response.get_json() == {"error": "invalid service token"}
    assert stored_row(client, booking_id) == before
