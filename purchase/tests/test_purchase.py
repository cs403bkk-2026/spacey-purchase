from datetime import UTC, datetime, timedelta, timezone

import pytest

from purchase.api import parse_time, parse_window
from purchase.booking import (
    calculate_booking_price_cents,
    create_booking,
    get_booking,
    mark_paid,
)
from purchase.member import is_valid_email, is_valid_password, subscribe
from purchase.space import create_space


@pytest.mark.parametrize(
    "hourly_rate_cents,seconds,expected_cents",
    [
        (1500, 1800, 750),
        (1000, 1200, 333),
        (100, 18, 1),
        (100, 17, 0),
        (3600, 1.9, 1),
        (0, 3600, 0),
    ],
)
def test_booking_price_examples(hourly_rate_cents, seconds, expected_cents):
    start = datetime(2030, 1, 1, tzinfo=UTC)

    assert (
        calculate_booking_price_cents(
            hourly_rate_cents, start, start + timedelta(seconds=seconds)
        )
        == expected_cents
    )


@pytest.mark.parametrize(
    "email,expected",
    [
        ("member@example.com", True),
        ("  member@example.com  ", True),
        ("member@example", False),
        ("member example@example.com", False),
        ("", False),
        (None, False),
        (42, False),
    ],
)
def test_email_validation_examples(email, expected):
    assert is_valid_email(email) is expected


@pytest.mark.parametrize(
    "password,expected",
    [
        ("12345678", True),
        ("1234567", False),
        ("", False),
        (None, False),
        (12345678, False),
    ],
)
def test_password_validation_examples(password, expected):
    assert is_valid_password(password) is expected


@pytest.mark.parametrize("name", ["annabel", " ", "guest"])
def test_subscribe_is_a_tombstone(name):
    assert subscribe(None, name) == (
        {"error": "subscriptions have been removed"},
        410,
    )


def test_a_former_subscribers_booking_is_unpaid_at_full_price(cur):
    cur.execute(
        "INSERT INTO subscriptions (member) VALUES ('annabel') "
        "ON CONFLICT (member) DO UPDATE SET active = TRUE"
    )
    space, _ = create_space(cur, "Test Room", 4, 2500)
    start = datetime(2030, 1, 1, 10, tzinfo=UTC)

    booking, status = create_booking(
        cur, space["id"], "  ANNABEL ", start, start + timedelta(hours=1), 1, None
    )

    assert status == 201
    assert booking["paid"] is False
    assert booking["amount_cents"] == 2500
    assert booking["card_last4"] is None


def test_an_old_covered_booking_reads_paid_at_zero(cur):
    space, _ = create_space(cur, "Free Room", 4, 0)
    start = datetime(2030, 1, 1, 10, tzinfo=UTC)
    booking, _ = create_booking(
        cur, space["id"], "annabel", start, start + timedelta(hours=1), 1, None
    )
    mark_paid(cur, booking["id"], None)

    covered = get_booking(cur, booking["id"])

    assert covered["paid"] is True
    assert covered["amount_cents"] == 0
    assert covered["card_last4"] is None


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2030-01-01T10:00:00+07:00", datetime(2030, 1, 1, 3, tzinfo=UTC)),
        ("2030-01-01T03:00:00Z", datetime(2030, 1, 1, 3, tzinfo=UTC)),
        ("2030-01-01T10:00:00", None),
        ("tomorrow", None),
        ("", None),
        (None, None),
        (42, None),
    ],
)
def test_parse_time_examples(value, expected):
    assert parse_time(value) == expected


@pytest.mark.parametrize(
    "args,expected,error",
    [
        ({}, None, None),
        (
            {
                "start_time": "2030-01-01T10:00:00+07:00",
                "end_time": "2030-01-01T11:00:00+07:00",
            },
            (
                datetime(2030, 1, 1, 10, tzinfo=timezone(timedelta(hours=7))),
                datetime(2030, 1, 1, 11, tzinfo=timezone(timedelta(hours=7))),
            ),
            None,
        ),
        ({"start_time": "2030-01-01T10:00:00+07:00"}, None, "must be given together"),
        ({"end_time": "2030-01-01T11:00:00+07:00"}, None, "must be given together"),
        (
            {
                "start_time": "2030-01-01T10:00:00",
                "end_time": "2030-01-01T11:00:00+07:00",
            },
            None,
            "must be given together",
        ),
        (
            {
                "start_time": "2030-01-01T11:00:00+07:00",
                "end_time": "2030-01-01T10:00:00+07:00",
            },
            None,
            "end_time must be after start_time",
        ),
        (
            {
                "start_time": "2030-01-01T10:00:00+07:00",
                "end_time": "2030-01-01T10:00:00+07:00",
            },
            None,
            "end_time must be after start_time",
        ),
    ],
)
def test_parse_window_examples(args, expected, error):
    window, message = parse_window(args)
    if error:
        assert window is None
        assert error in message
    else:
        assert message is None
        assert window == expected
