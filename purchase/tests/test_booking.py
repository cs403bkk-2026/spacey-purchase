from datetime import UTC, datetime

from purchase.booking import create_booking, get_booking, mark_paid
from purchase.space import create_space


def at(hour, minute=0):
    return datetime(2030, 1, 1, hour, minute, tzinfo=UTC)


def make_space(cur, price_cents=1500):
    space, _ = create_space(cur, "Test Room", 4, price_cents)
    return space["id"]


def book(cur, space_id, start_time, end_time):
    # party_size 1, guest
    return create_booking(cur, space_id, "Ann", start_time, end_time, 1, None)


def test_create_booking_stores_an_unpaid_booking_at_its_price(cur):
    space_id = make_space(cur, price_cents=1500)

    booking, status = book(cur, space_id, at(10), at(12))

    assert status == 201
    assert booking["paid"] is False
    assert booking["amount_cents"] == 3000
    assert booking["card_last4"] is None


def test_create_booking_refuses_an_overlapping_slot(cur):
    space_id = make_space(cur)
    book(cur, space_id, at(10), at(11))

    assert book(cur, space_id, at(10, 30), at(11, 30)) == (
        {"error": "space is already booked for that time"},
        409,
    )


def test_back_to_back_bookings_are_allowed(cur):
    space_id = make_space(cur)
    book(cur, space_id, at(10), at(11))

    _, status = book(cur, space_id, at(11), at(12))

    assert status == 201


def test_create_booking_for_a_missing_space_is_not_found(cur):
    assert book(cur, 0, at(10), at(11)) == ({"error": "space not found"}, 404)


def test_get_booking_returns_none_when_missing(cur):
    assert get_booking(cur, 0) is None


def test_mark_paid_stores_the_last_four_digits(cur):
    booking, _ = book(cur, make_space(cur), at(10), at(11))

    paid = mark_paid(cur, booking["id"], "4242")

    for row in (paid, get_booking(cur, booking["id"])):
        assert row["paid"] is True
        assert row["card_last4"] == "4242"
