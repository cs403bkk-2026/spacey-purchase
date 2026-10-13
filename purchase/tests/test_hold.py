import os
import threading
import time
from datetime import timedelta

import psycopg
import pytest
from psycopg.rows import dict_row
from purchase_helpers import BOOKING_KEYS, NOW, login, make_client, register, slot

import purchase.booking

TAKEN = {"error": "space is already booked for that time"}
HOLD_EXPIRED = {"error": "booking hold has expired"}


def book(client, start_hours=1, end_hours=2, space_id=1, **body):
    return client.post(
        f"/spaces/{space_id}/bookings",
        json={"member": "annabel", **slot(start_hours, end_hours), **body},
    )


def book_id(client, start_hours=1, end_hours=2, **body):
    response = book(client, start_hours, end_hours, **body)
    assert response.status_code == 201
    return response.get_json()["id"]


def age(client, booking_id, interval):
    """Set created_at to `interval` ago, e.g. "15 minutes"."""
    with client.application.db.cursor() as cur:
        cur.execute(
            "UPDATE bookings SET created_at = now() - %s::interval WHERE id = %s",
            (interval, booking_id),
        )


def expire(client, booking_id):
    """Age the booking past its hold, then let a slot check mark it expired."""
    age(client, booking_id, "16 minutes")
    client.get("/spaces/booked-ids")
    assert stored_status(client, booking_id) == "expired"


def payable(client, booking_id):
    return client.get(f"/bookings/{booking_id}/payable")


def pay(client, booking_id, card_last4="4242"):
    """What the hub sends after Payment answers success."""
    return client.post(f"/bookings/{booking_id}/paid", json={"card_last4": card_last4})


def stored_rows(client):
    with client.application.db.cursor() as cur:
        cur.execute("SELECT * FROM bookings ORDER BY id")
        return cur.fetchall()


def stored_row(client, booking_id):
    with client.application.db.cursor() as cur:
        cur.execute("SELECT * FROM bookings WHERE id = %s", (booking_id,))
        return cur.fetchone()


def stored_status(client, booking_id):
    return stored_row(client, booking_id)["status"]


def slot_holders(client, space_id=1):
    """Ids of the bookings that hold a slot of the space, as stored."""
    with client.application.db.cursor() as cur:
        cur.execute(
            "SELECT id FROM bookings "
            "WHERE space_id = %s AND status IN ('pending', 'completed') ORDER BY id",
            (space_id,),
        )
        return [row["id"] for row in cur.fetchall()]


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
    pytest.fail("the second side never waited for the first side's lock")


def test_hold_active_at_14_minutes_50():
    client = make_client()
    booking_id = book_id(client)
    age(client, booking_id, "14 minutes 50 seconds")

    response = payable(client, booking_id)
    again = book(client)

    assert response.status_code == 200
    assert response.get_json()["status"] == "pending"
    assert again.status_code == 409
    assert again.get_json() == TAKEN


def test_payable_after_15_minutes_is_409():
    client = make_client()
    booking_id = book_id(client)
    age(client, booking_id, "15 minutes")

    response = payable(client, booking_id)

    assert response.status_code == 409
    assert response.get_json() == HOLD_EXPIRED
    assert stored_status(client, booking_id) == "expired"


def book_the_slot_again(client):
    return book(client, -1, 1)


def booked_ids_now(client):
    return client.get("/spaces/booked-ids")


def booked_ids_window(client):
    return client.get("/spaces/booked-ids", query_string=slot(-1, 1))


@pytest.mark.parametrize(
    "first_call",
    [book_the_slot_again, booked_ids_now, booked_ids_window],
    ids=["booking", "booked_ids_now", "booked_ids_window"],
)
def test_expired_hold_frees_the_slot(first_call):
    client = make_client()
    old_id = book_id(client, -1, 1)
    age(client, old_id, "15 minutes")

    answer = first_call(client)

    if first_call is book_the_slot_again:
        rebooked = answer
    else:
        assert answer.get_json() == {"space_ids": []}
        rebooked = book_the_slot_again(client)
    assert rebooked.status_code == 201
    assert slot_holders(client) == [rebooked.get_json()["id"]]
    assert stored_status(client, old_id) == "expired"


def test_space_bookings_leave_out_expired_holds():
    client = make_client()
    expired_id = book_id(client, 1, 2)
    pending_id = book_id(client, 3, 4)
    completed_id = book_id(client, 5, 6)
    pay(client, completed_id)
    age(client, expired_id, "15 minutes")

    response = client.get("/spaces/1/bookings")

    listed = [booking["id"] for booking in response.get_json()["bookings"]]
    assert response.status_code == 200
    assert listed == [pending_id, completed_id]


@pytest.mark.parametrize("read", ["all", "user", "one"])
def test_expired_hold_reads_as_unpaid(read):
    client = make_client()
    register(client)
    user = login(client).get_json()
    booking_id = book_id(client, user_id=user["id"])
    expire(client, booking_id)
    paths = {
        "all": "/bookings",
        "user": f"/users/{user['id']}/bookings",
        "one": f"/bookings/{booking_id}",
    }

    response = client.get(paths[read])

    body = response.get_json()
    booking = body if read == "one" else body["bookings"][0]
    assert response.status_code == 200
    assert booking.keys() == BOOKING_KEYS
    assert booking["id"] == booking_id
    assert booking["paid"] is False


def test_a_failed_attempt_does_not_restart_the_hold():
    client = make_client()
    booking_id = book_id(client)
    age(client, booking_id, "14 minutes")
    created_at = stored_row(client, booking_id)["created_at"]

    first = payable(client, booking_id)
    # Payment answered "failed", so the hub sends no /paid.
    unchanged = stored_row(client, booking_id)["created_at"]
    with client.application.db.cursor() as cur:
        cur.execute(
            "UPDATE bookings SET created_at = created_at - interval '1 minute' "
            "WHERE id = %s",
            (booking_id,),
        )
    retry = payable(client, booking_id)

    assert first.status_code == 200
    assert unchanged == created_at
    assert retry.status_code == 409
    assert retry.get_json() == HOLD_EXPIRED


@pytest.mark.parametrize("marked", [True, False], ids=["expired", "overdue_not_marked"])
def test_late_success_on_a_free_slot_confirms(marked):
    client = make_client()
    booking_id = book_id(client)
    age(client, booking_id, "14 minutes")
    assert payable(client, booking_id).status_code == 200
    if marked:
        expire(client, booking_id)
    else:
        age(client, booking_id, "16 minutes")
        assert stored_status(client, booking_id) == "pending"

    response = pay(client, booking_id)

    body = response.get_json()
    assert response.status_code == 200
    assert body["paid"] is True
    assert body["card_last4"] == "4242"
    assert stored_status(client, booking_id) == "completed"
    assert book(client).get_json() == TAKEN


def a_late_success_on_a_taken_slot(client):
    """A hold that ran out after /payable, its slot booked by someone else."""
    booking_id = book_id(client)
    age(client, booking_id, "14 minutes")
    assert payable(client, booking_id).status_code == 200
    age(client, booking_id, "16 minutes")
    book_id(client, member="bob")
    return booking_id


def test_late_success_on_a_taken_slot_is_409():
    client = make_client()
    booking_id = a_late_success_on_a_taken_slot(client)
    rows = stored_rows(client)

    response = pay(client, booking_id)

    assert response.status_code == 409
    assert response.get_json() == HOLD_EXPIRED
    assert stored_rows(client) == rows


def a_confirmed_late_success(client):
    booking_id = book_id(client)
    expire(client, booking_id)
    assert pay(client, booking_id, "4242").status_code == 200
    return booking_id


def a_refused_late_success(client):
    booking_id = a_late_success_on_a_taken_slot(client)
    assert pay(client, booking_id, "4242").status_code == 409
    return booking_id


@pytest.mark.parametrize(
    ("late_success", "status"),
    [(a_confirmed_late_success, 200), (a_refused_late_success, 409)],
    ids=["confirmed", "refused"],
)
def test_repeated_late_success_same_answer(late_success, status):
    client = make_client()
    booking_id = late_success(client)
    first = client.get(f"/bookings/{booking_id}").get_json()
    rows = stored_rows(client)

    response = pay(client, booking_id, "1111")

    assert response.status_code == status
    if status == 200:
        assert response.get_json() == first
        assert first["card_last4"] == "4242"
    else:
        assert response.get_json() == HOLD_EXPIRED
    assert stored_rows(client) == rows


def pay_on(conn, booking_id):
    return purchase.booking.mark_paid(conn.cursor(), booking_id, "4242")


def book_on(conn):
    row, status = purchase.booking.create_booking(
        conn.cursor(),
        1,
        "bob",
        NOW + timedelta(hours=1),
        NOW + timedelta(hours=2),
        1,
        None,
    )
    assert status == 201
    return row


@pytest.mark.parametrize(
    "paid_first", [True, False], ids=["paid_first", "booking_first"]
)
def test_late_success_racing_a_new_booking(paid_first):
    client = make_client()
    expired_id = book_id(client)
    expire(client, expired_id)
    answers = []

    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as first:
        first.execute("SELECT 1")
        if paid_first:
            winner_id = pay_on(first, expired_id)["id"]
            second = threading.Thread(target=lambda: answers.append(book(client)))
        else:
            winner_id = book_on(first)["id"]
            second = threading.Thread(
                target=lambda: answers.append(pay(client, expired_id))
            )
        second.start()
        wait_until_waiting_on_a_lock(client.application.db.info.backend_pid)
        first.commit()
    second.join(timeout=5)

    assert not second.is_alive()
    assert answers[0].status_code == 409
    assert answers[0].get_json() == (TAKEN if paid_first else HOLD_EXPIRED)
    assert slot_holders(client) == [winner_id]


def a_paid_booking(client):
    booking_id = book_id(client)
    pay(client, booking_id)
    return booking_id


def an_old_booking_paid_at_zero(client):
    """As the copy writes a booking a subscription covered: paid at 0, no card."""
    with client.application.db.cursor() as cur:
        cur.execute(
            "INSERT INTO bookings "
            "(space_id, member, status, start_time, end_time, amount_cents) "
            "VALUES (1, 'annabel', 'completed', %s, %s, 0) RETURNING id",
            (NOW + timedelta(hours=1), NOW + timedelta(hours=2)),
        )
        return cur.fetchone()["id"]


@pytest.mark.parametrize(
    "make_booking",
    [a_paid_booking, an_old_booking_paid_at_zero],
    ids=["paid", "old_paid_at_zero"],
)
def test_completed_never_expires(make_booking):
    client = make_client()
    booking_id = make_booking(client)
    age(client, booking_id, "1 day")

    response = payable(client, booking_id)

    assert response.status_code == 200
    assert response.get_json()["status"] == "completed"
    assert book(client).get_json() == TAKEN


def test_first_check_expires_only_overdue_pending():
    client = make_client()
    old_pending = book_id(client, 1, 2)
    recent_pending = book_id(client, 3, 4)
    old_completed = book_id(client, 5, 6)
    pay(client, old_completed)
    old_cancelled = book_id(client, 7, 8)
    client.delete(f"/bookings/{old_cancelled}")
    for booking_id in (old_pending, old_completed, old_cancelled):
        age(client, booking_id, "16 minutes")

    response = payable(client, recent_pending)

    assert response.status_code == 200
    assert {
        booking_id: stored_status(client, booking_id)
        for booking_id in (old_pending, recent_pending, old_completed, old_cancelled)
    } == {
        old_pending: "expired",
        recent_pending: "pending",
        old_completed: "completed",
        old_cancelled: "cancelled",
    }


def test_expired_hold_unlock_is_402():
    client = make_client()
    booking_id = book_id(client)
    expire(client, booking_id)

    response = client.get(f"/bookings/{booking_id}/unlockable")

    assert response.status_code == 402
    assert response.get_json() == {"error": "booking is not paid"}


def test_cancel_expired_hold_twice_same_row():
    client = make_client()
    booking_id = book_id(client)
    expire(client, booking_id)
    before = client.get(f"/bookings/{booking_id}").get_json()

    first = client.delete(f"/bookings/{booking_id}")
    second = client.delete(f"/bookings/{booking_id}")

    assert first.status_code == 200
    assert first.get_json() == before
    assert second.status_code == 404
    assert second.get_json() == {"error": "booking not found"}
    assert stored_status(client, booking_id) == "cancelled"


def test_delete_space_with_expired_hold_is_409():
    client = make_client()
    client.post("/spaces", json={"name": "Meeting Room A", "capacity": 6})
    booking = book(client, space_id=2)
    booking_id = booking.get_json()["id"]
    expire(client, booking_id)
    rows = stored_rows(client)

    response = client.delete("/spaces/2")

    assert response.status_code == 409
    assert response.get_json() == {"error": "space has bookings, cancel them first"}
    assert stored_rows(client) == rows
