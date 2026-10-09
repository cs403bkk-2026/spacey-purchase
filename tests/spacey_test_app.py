from datetime import date, datetime, timedelta, timezone

from app import create_app
from purchase.tests.purchase_helpers import make_client

def test_root_redirects_to_the_frontend():
    client = make_client()

    response = client.get("/")

    assert response.status_code == 302
    assert response.headers["Location"] == "/app/"

def test_health_reports_running_revision(monkeypatch):
    monkeypatch.setenv("APP_REVISION", "test-revision")
    client = make_client()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {
        "revision": "test-revision",
        "status": "ok",
    }

def test_member_journey():
    app = create_app(reset_on_start=True)
    client = app.test_client()

    space = client.post(
        "/spaces",
        json={"name": "Journey Room", "capacity": 4, "price_cents": 1500},
    )
    assert space.status_code == 201
    assert space.get_json() == {
        "id": 2,
        "name": "Journey Room",
        "capacity": 4,
        "price_cents": 1500,
    }

    registered = client.post(
        "/register", json={"email": "Journey@Example.com", "password": "journey22"}
    )
    assert registered.status_code == 201
    assert registered.get_json() == {"id": 1, "email": "journey@example.com"}
    assert client.post(
        "/login", json={"email": "journey@example.com", "password": "journey22"}
    ).status_code == 200

    start = (datetime.now(timezone.utc) + timedelta(hours=2)).replace(microsecond=0)
    end = start + timedelta(hours=2)
    start_local = start.astimezone(timezone(timedelta(hours=7))).isoformat()
    end_local = end.astimezone(timezone(timedelta(hours=7))).isoformat()
    booking_response = client.post(
        "/spaces/2/bookings",
        json={
            "member": " journey ",
            "party_size": 2,
            "start_time": start_local,
            "end_time": end_local,
        },
    )
    assert booking_response.status_code == 201
    booking = booking_response.get_json()
    booking.pop("created_at")
    assert booking == {
        "id": 1,
        "space_id": 2,
        "member": "journey",
        "paid": False,
        "amount_cents": 3000,
        "user_id": 1,
        "card_last4": None,
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
    }

    overlap = client.post(
        "/spaces/2/bookings",
        json={
            "member": "journey",
            "party_size": 2,
            "start_time": start_local,
            "end_time": end_local,
        },
    )
    assert overlap.status_code == 409
    assert overlap.get_json() == {"error": "space is already booked for that time"}
    expected_booking = {**booking, "paid": False}
    booking_get = client.get("/bookings/1").get_json()
    booking_get.pop("created_at")
    assert booking_get == expected_booking
    my_bookings = client.get("/me/bookings").get_json()["bookings"][0]
    my_bookings.pop("created_at")
    assert my_bookings == expected_booking

    card = {
        "card_number": "4242424242424242",
        "expiry": f"12/{(date.today().year + 1) % 100:02d}",
        "cvc": "123",
    }
    paid_response = client.post("/bookings/1/pay", json=card)
    assert paid_response.status_code == 200
    paid = paid_response.get_json()
    assert paid["paid"] is True
    assert paid["card_last4"] == "4242"
    first_code = client.post("/bookings/1/unlock").get_json()["access_code"]
    second_code = client.post("/bookings/1/unlock").get_json()["access_code"]
    assert first_code == second_code
    metrics = client.get("/metrics").get_json()
    assert metrics["bookings"] == 1
    assert metrics["paid_bookings"] == 1
    assert metrics["revenue_cents"] == 3000

    assert client.delete("/bookings/1").status_code == 200
    assert client.get("/bookings/1").status_code == 404

def test_health_reports_error_when_database_is_unreachable():
    app = create_app(reset_on_start=True)
    app.db.close()  # simulate a lost/broken database connection
    client = app.test_client()

    response = client.get("/health")

    assert response.status_code == 503
    assert response.get_json() == {
        "status": "error",
        "error": "database unreachable",
    }

def test_startup_fails_fast_with_a_clear_message_for_a_bad_database_url():
    bad_url = "postgresql://spacey:spacey@localhost:1/spacey"

    with pytest.raises(SystemExit) as exc_info:
        create_app(database_url=bad_url)

    message = str(exc_info.value)
    assert bad_url in message
    assert "docker compose up db -d" in message

def test_dashboard_redirects_to_the_grafana_report():
    client = make_client()

    response = client.get("/dashboard")

    assert response.status_code == 302
    assert response.headers["Location"] == "https://grafana.cs403bkk26.space/d/spacey-reporting"

def test_reporting_url_can_be_configured(monkeypatch):
    monkeypatch.setattr("app.REPORTING_URL", "https://example.test/report")
    client = make_client()

    assert client.get("/dashboard").headers["Location"] == "https://example.test/report"

# --- PT-016: payments table ---

def test_payments_migration_can_run_twice():
    from payment.migrations import run_migrations
    app = create_app(reset_on_start=True)
    run_migrations(app.db)
    run_migrations(app.db)

def test_payment_is_inserted_and_fetched_by_booking():
    from payment.repository import get_payments_for_booking, insert_payment
    app = create_app(reset_on_start=True)
    row = insert_payment(app.db, 7, 2500, "success", card_last4="4242")
    assert row["id"] == 1 and row["currency"] == "USD" and row["status"] == "success"
    insert_payment(app.db, 8, 1000, "failed", reason="declined")
    found = get_payments_for_booking(app.db, 7)
    assert [p["id"] for p in found] == [1]
    assert found[0]["card_last4"] == "4242"
    assert "card_number" not in found[0] and "cvc" not in found[0]

def test_payments_reject_values_outside_the_enums():
    import psycopg
    from payment.repository import insert_payment
    app = create_app(reset_on_start=True)
    with pytest.raises(psycopg.errors.InvalidTextRepresentation):
        insert_payment(app.db, 1, 100, "refunded")
    with pytest.raises(psycopg.errors.InvalidTextRepresentation):
        insert_payment(app.db, 1, 100, "success", currency="EUR")
