from pathlib import Path

import pytest
import yaml

from purchase.tests.purchase_helpers import make_client, pay, slot

# --- HELPERS ---


def add_space(client, name="Test Space", capacity=1, price=2500):
    with client.application.db.cursor() as cur:
        cur.execute(
            "INSERT INTO spaces (name, capacity, price_cents) "
            "VALUES (%s, %s, %s) RETURNING id",
            (name, capacity, price),
        )
        return cur.fetchone()["id"]


def add_booking(
    client,
    space_id=1,
    member="annabel",
    hours=(1, 2),
    status="pending",
    amount=2500,
    card_last4=None,
):
    times = slot(*hours)
    with client.application.db.cursor() as cur:
        cur.execute(
            "INSERT INTO bookings "
            "(space_id, member, start_time, end_time, amount_cents, status, card_last4) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                space_id,
                member,
                times["start_time"],
                times["end_time"],
                amount,
                status,
                card_last4,
            ),
        )
        return cur.fetchone()["id"]


# --- 9 NEW TESTS ---


def test_bookings_equal_paid_plus_unpaid_for_every_status():
    client = make_client()
    add_booking(client, hours=(1, 2), status="pending")
    add_booking(client, hours=(2, 3), status="completed")
    add_booking(client, hours=(3, 4), status="expired")
    add_booking(client, hours=(4, 5), status="cancelled")

    data = client.get("/metrics").get_json()

    assert data["bookings"] == 3
    assert data["paid_bookings"] == 1
    assert data["unpaid_bookings"] == 2


def test_metrics_skip_cancelled_bookings():
    client = make_client()
    add_booking(client, hours=(1, 2), status="completed", amount=1500)
    add_booking(client, hours=(2, 3), status="pending", amount=1500)
    add_booking(
        client, hours=(3, 4), status="cancelled", amount=1500, card_last4="4242"
    )
    add_booking(client, hours=(4, 5), status="cancelled", amount=1500)

    data = client.get("/metrics").get_json()

    assert data["bookings"] == 2
    assert data["revenue_cents"] == 1500
    assert data["payment_conversion"] == 0.5


def test_a_member_with_only_cancelled_bookings_is_not_counted():
    client = make_client()
    add_booking(client, member="annabel", hours=(1, 2), status="cancelled")
    add_booking(client, member="annabel", hours=(2, 3), status="cancelled")
    add_booking(client, member="gregory", hours=(3, 4), status="pending")

    data = client.get("/metrics").get_json()

    assert data["members"] == 1
    assert data["repeat_member_rate"] == 0.0


def test_a_cancelled_booking_adds_no_booked_hours():
    client = make_client()
    add_booking(client, hours=(1, 25), status="cancelled")
    assert client.get("/metrics").get_json()["utilization"] == 0.0


def test_an_expired_booking_adds_no_booked_hours():
    client = make_client()
    add_booking(client, hours=(1, 25), status="expired")
    add_booking(client, hours=(1, 25), status="completed")
    assert client.get("/metrics").get_json()["utilization"] == pytest.approx(24 / 168)


def test_revenue_by_space_skips_cancelled_revenue():
    client = make_client()
    space_id = add_space(client, name="Ghost Room")
    add_booking(
        client, space_id=space_id, hours=(1, 2), status="cancelled", amount=1000
    )

    revenue_by_space = client.get("/metrics").get_json()["revenue_by_space"]
    space_metrics = next(s for s in revenue_by_space if s["id"] == space_id)
    assert space_metrics["revenue_cents"] == 0


def test_a_completed_booking_at_zero_counts_as_paid_without_revenue():
    client = make_client()
    add_booking(client, hours=(1, 2), status="completed", amount=0)

    data = client.get("/metrics").get_json()

    assert data["paid_bookings"] == 1
    assert data["revenue_cents"] == 0
    assert data["avg_revenue_cents_per_paid_booking"] == 0


def test_metrics_answer_has_the_contract_keys():
    client = make_client()
    with client.application.db.cursor() as cur:
        cur.execute("DELETE FROM spaces")

    data = client.get("/metrics").get_json()

    yaml_path = Path(__file__).parent.parent.parent / "openapi.yaml"
    with yaml_path.open() as f:
        spec = yaml.safe_load(f)

    expected_keys = set(
        spec["paths"]["/metrics"]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]["properties"].keys()
    )
    assert set(data.keys()) == expected_keys


@pytest.mark.parametrize("setup_client", ["missing", "wrong"])
def test_metrics_without_a_valid_token_answers_403(setup_client):
    client = make_client()
    if setup_client == "missing":
        client.environ_base.pop("HTTP_AUTHORIZATION", None)
        response = client.get("/metrics")
    else:
        response = client.get(
            "/metrics", headers={"Authorization": "Bearer wrong-token"}
        )

    assert response.status_code == 403
    assert response.get_json() == {"error": "invalid service token"}


# --- 16 PORTED TESTS ---


def test_metrics_reports_spaces_bookings_and_members():
    client = make_client()
    add_space(client, name="Meeting Room A", capacity=6)
    add_booking(client, space_id=1, member="annabel", hours=(1, 2))
    add_booking(client, space_id=2, member="gregory", hours=(1, 2))

    response = client.get("/metrics")

    assert response.status_code == 200
    body = response.get_json()
    assert body["spaces"] == 2
    assert body["bookings"] == 2
    assert body["paid_bookings"] == 0
    assert body["unpaid_bookings"] == 2
    assert body["members"] == 2
    assert body["revenue_cents"] == 0


def test_metrics_splits_paid_and_unpaid_bookings():
    client = make_client()
    first_id = add_booking(client, space_id=1, member="annabel", hours=(1, 2))
    add_booking(client, space_id=1, member="gregory", hours=(3, 4))
    add_booking(client, space_id=1, member="flurina", hours=(5, 6))

    pay(client, first_id)

    body = client.get("/metrics").get_json()

    assert body["bookings"] == 3
    assert body["paid_bookings"] == 1
    assert body["unpaid_bookings"] == 2


def test_metrics_with_no_bookings_reports_zero_for_each_count():
    client = make_client()
    body = client.get("/metrics").get_json()
    assert body["bookings"] == 0
    assert body["paid_bookings"] == 0
    assert body["unpaid_bookings"] == 0


def test_metrics_reports_revenue_from_paid_bookings():
    client = make_client()
    space_id = add_space(client, name="Meeting Room A", capacity=6, price=1500)
    booking_id = add_booking(
        client, space_id=space_id, member="gregory", hours=(1, 2), amount=1500
    )

    assert client.get("/metrics").get_json()["revenue_cents"] == 0

    pay(client, booking_id)
    response = client.get("/metrics")

    assert response.status_code == 200
    body = response.get_json()
    assert body["revenue_cents"] == 1500


def test_utilization_over_the_next_7_days():
    client = make_client()
    add_booking(client, space_id=1, member="annabel", hours=(1, 25))
    body = client.get("/metrics").get_json()
    assert body["utilization"] == pytest.approx(24 / 168)


def test_utilization_ignores_bookings_outside_the_next_7_days():
    client = make_client()
    add_booking(client, space_id=1, member="annabel", hours=(200, 201))
    body = client.get("/metrics").get_json()
    assert body["utilization"] == 0.0


def test_utilization_is_zero_with_no_spaces():
    client = make_client()
    client.delete("/spaces/1")
    body = client.get("/metrics").get_json()
    assert body["spaces"] == 0
    assert body["utilization"] == 0.0


def test_repeat_member_rate():
    client = make_client()
    add_space(client, name="Room B", capacity=4)
    add_booking(client, space_id=1, member="annabel", hours=(1, 2))
    add_booking(client, space_id=2, member="annabel", hours=(3, 4))
    add_booking(client, space_id=2, member="gregory", hours=(5, 6))

    body = client.get("/metrics").get_json()

    assert body["members"] == 2
    assert body["repeat_member_rate"] == pytest.approx(1 / 2)


def test_repeat_member_rate_is_zero_with_no_bookings():
    client = make_client()
    body = client.get("/metrics").get_json()
    assert body["repeat_member_rate"] == 0.0


def test_payment_conversion():
    client = make_client()
    first_id = add_booking(client, space_id=1, member="annabel", hours=(1, 2))
    add_booking(client, space_id=1, member="gregory", hours=(3, 4))
    pay(client, first_id)

    body = client.get("/metrics").get_json()

    assert body["payment_conversion"] == pytest.approx(0.5)


def test_payment_conversion_is_zero_with_no_bookings():
    client = make_client()
    body = client.get("/metrics").get_json()
    assert body["payment_conversion"] == 0.0


def test_average_revenue_per_paid_booking():
    client = make_client()
    add_space(client, name="Room B", capacity=4, price=1000)
    first_id = add_booking(
        client, space_id=2, member="annabel", hours=(1, 2), amount=1000
    )
    second_id = add_booking(
        client, space_id=2, member="gregory", hours=(3, 4), amount=1000
    )

    pay(client, first_id)
    pay(client, second_id)

    body = client.get("/metrics").get_json()

    assert body["avg_revenue_cents_per_paid_booking"] == 1000


def test_average_revenue_per_paid_booking_is_zero_with_no_paid_bookings():
    client = make_client()
    body = client.get("/metrics").get_json()
    assert body["avg_revenue_cents_per_paid_booking"] == 0


def test_revenue_by_space():
    client = make_client()
    add_space(client, name="Room B", capacity=4, price=1000)
    add_booking(client, space_id=1, member="annabel", hours=(1, 2))
    paid_id = add_booking(
        client, space_id=2, member="gregory", hours=(3, 4), amount=1000
    )
    pay(client, paid_id)

    body = client.get("/metrics").get_json()

    by_id = {row["id"]: row for row in body["revenue_by_space"]}
    assert by_id[1]["name"] == "Founders Desk"
    assert by_id[1]["revenue_cents"] == 0
    assert by_id[2]["name"] == "Room B"
    assert by_id[2]["revenue_cents"] == 1000


def test_revenue_by_space_includes_a_space_with_no_bookings():
    client = make_client()
    body = client.get("/metrics").get_json()
    assert body["revenue_by_space"] == [
        {"id": 1, "name": "Founders Desk", "revenue_cents": 0}
    ]


def test_revenue_stays_the_same_when_the_space_price_changes_later():
    client = make_client()
    space_id = add_space(client, name="Meeting Room A", capacity=6, price=500)
    booking_id = add_booking(
        client, space_id=space_id, member="gregory", hours=(1, 2), amount=500
    )
    pay(client, booking_id)

    assert client.get("/metrics").get_json()["revenue_cents"] == 500

    with client.application.db.cursor() as cur:
        cur.execute("UPDATE spaces SET price_cents = 2000 WHERE id = %s", (space_id,))

    assert client.get("/metrics").get_json()["revenue_cents"] == 500

    later_id = add_booking(
        client, space_id=space_id, member="annabel", hours=(3, 4), amount=2000
    )
    pay(client, later_id)

    assert client.get("/metrics").get_json()["revenue_cents"] == 2500
