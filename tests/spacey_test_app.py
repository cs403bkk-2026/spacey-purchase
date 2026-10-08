import threading
from datetime import datetime, timedelta, timezone

import pytest

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

# A mocked, obviously-fake card - always valid, never a real payment.
VALID_CARD = {"card_number": "4242424242424242", "expiry": "12/30", "cvc": "123"}

def test_root_redirects_to_the_frontend():
    client = make_client()

    response = client.get("/")

    assert response.status_code == 302
    assert response.headers["Location"] == "/app/"

def test_three_hours_cost_three_times_one_hour():
    client = make_client()
    client.post(
        "/spaces",
        json={"name": "Meeting Room A", "capacity": 6, "price_cents": 1500},
    )

    one_hour = client.post(
        "/spaces/2/bookings", json={"member": "annabel", **slot(1, 2)}
    ).get_json()
    three_hours = client.post(
        "/spaces/2/bookings", json={"member": "gregory", **slot(4, 7)}
    ).get_json()

    assert one_hour["amount_cents"] == 1500
    assert three_hours["amount_cents"] == 4500

def test_half_an_hour_costs_half_the_hourly_rate():
    client = make_client()
    client.post(
        "/spaces",
        json={"name": "Meeting Room A", "capacity": 6, "price_cents": 1500},
    )

    booking = client.post(
        "/spaces/2/bookings", json={"member": "annabel", **slot(1, 1.5)}
    ).get_json()

    assert booking["amount_cents"] == 750

def test_an_uneven_duration_is_rounded_to_whole_cents():
    client = make_client()
    client.post(
        "/spaces",
        json={"name": "Meeting Room A", "capacity": 6, "price_cents": 1000},
    )

    # 20 minutes at $10.00/hour is 333.33 cents
    booking = client.post(
        "/spaces/2/bookings",
        json={"member": "annabel", **slot(1, 1 + 1 / 3)},
    ).get_json()

    assert booking["amount_cents"] == 333

def test_booking_responses_include_the_amount_charged():
    client = make_client()
    client.post("/spaces", json={"name": "Meeting Room A", "capacity": 6, "price_cents": 1500})

    created = client.post(
        "/spaces/2/bookings", json={"member": "annabel", **slot(1, 2)}
    ).get_json()
    assert created["amount_cents"] == 1500

    # every other place a booking shows up agrees with what was charged
    assert client.get(f"/bookings/{created['id']}").get_json()["amount_cents"] == 1500
    assert client.get("/bookings").get_json()["bookings"][0]["amount_cents"] == 1500
    assert client.get("/spaces/2/bookings").get_json()["bookings"][0]["amount_cents"] == 1500
    paid = client.post(f"/bookings/{created['id']}/pay", json=VALID_CARD).get_json()
    assert paid["amount_cents"] == 1500
    cancelled = client.delete(f"/bookings/{created['id']}").get_json()
    assert cancelled["amount_cents"] == 1500

def test_a_price_change_after_booking_does_not_change_the_amount_shown():
    client = make_client()
    client.post("/spaces", json={"name": "Meeting Room A", "capacity": 6, "price_cents": 500})
    created = client.post(
        "/spaces/2/bookings", json={"member": "annabel", **slot(1, 2)}
    ).get_json()

    client.patch("/spaces/2", json={"price_cents": 2000})

    assert client.get(f"/bookings/{created['id']}").get_json()["amount_cents"] == 500

def test_health_reports_running_revision(monkeypatch):
    monkeypatch.setenv("APP_REVISION", "test-revision")
    client = make_client()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {
        "revision": "test-revision",
        "status": "ok",
    }

def test_list_spaces_returns_the_seed_space():
    client = make_client()

    response = client.get("/spaces")

    assert response.status_code == 200
    assert response.get_json() == {
        "spaces": [
            {
                "id": 1,
                "name": "Founders Desk",
                "capacity": 1,
                "price_cents": 2500,
                "available": True
            }
        ]
    }

def test_create_space_adds_a_new_space():
    client = make_client()

    response = client.post(
        "/spaces", json={"name": "Meeting Room A", "capacity": 6}
    )

    assert response.status_code == 201
    assert response.get_json() == {
        "id": 2,
        "name": "Meeting Room A",
        "capacity": 6,
        "price_cents": 0,
    }

    listed = client.get("/spaces").get_json()["spaces"]
    assert {
        "id": 2,
        "name": "Meeting Room A",
        "capacity": 6,
        "price_cents": 0,
        "available": True,
    } in listed

def test_create_space_with_a_price_stores_it():
    client = make_client()

    response = client.post(
        "/spaces",
        json={"name": "Meeting Room A", "capacity": 6, "price_cents": 1500},
    )

    assert response.status_code == 201
    assert response.get_json() == {
        "id": 2,
        "name": "Meeting Room A",
        "capacity": 6,
        "price_cents": 1500,
    }

def test_create_space_with_negative_price_is_rejected():
    client = make_client()

    response = client.post(
        "/spaces",
        json={"name": "Meeting Room A", "capacity": 6, "price_cents": -100},
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "price_cents must be a non-negative integer"
    }

def test_create_space_with_a_boolean_price_is_rejected():
    client = make_client()

    for price in [True, False]:
        response = client.post(
            "/spaces",
            json={"name": "Meeting Room A", "capacity": 6, "price_cents": price},
        )

        assert response.status_code == 400
        assert response.get_json() == {
            "error": "price_cents must be a non-negative integer"
        }

def test_create_space_without_capacity_is_rejected():
    client = make_client()

    response = client.post("/spaces", json={"name": "No Capacity Room"})

    assert response.status_code == 400
    assert response.get_json() == {"error": "name and capacity are required"}

def test_create_space_with_empty_name_is_rejected():
    client = make_client()

    for name in ["", "   ", 42]:
        response = client.post("/spaces", json={"name": name, "capacity": 6})

        assert response.status_code == 400
        assert response.get_json() == {"error": "name must not be empty"}

def test_create_space_with_invalid_capacity_is_rejected():
    client = make_client()

    for capacity in [0, -5, "6", 2.5, True]:
        response = client.post(
            "/spaces", json={"name": "Meeting Room A", "capacity": capacity}
        )

        assert response.status_code == 400
        assert response.get_json() == {
            "error": "capacity must be a whole number of at least 1"
        }

    # nothing got created - only the seed space is there
    assert len(client.get("/spaces").get_json()["spaces"]) == 1

def test_create_space_trims_spaces_around_the_name():
    client = make_client()

    response = client.post(
        "/spaces", json={"name": "  Meeting Room A  ", "capacity": 6}
    )

    assert response.status_code == 201
    assert response.get_json()["name"] == "Meeting Room A"

def test_create_booking_for_existing_space_succeeds():
    client = make_client()

    response = client.post(
        "/spaces/1/bookings", json={"member": "annabel", **slot(1, 2)}
    )

    assert response.status_code == 201
    body = response.get_json()
    body.pop("created_at")  # set by the database, checked in its own test
    assert body == {
        "id": 1,
        "space_id": 1,
        "member": "annabel",
        "paid": False,  # unpaid until /pay is called
        "amount_cents": 2500,  # Founders Desk is $25.00/hour, booked for 1 hour
        "user_id": None,  # not logged in
        "card_last4": None,  # not paid yet
        **slot(1, 2),
    }

def test_create_booking_for_unknown_space_returns_404():
    client = make_client()

    response = client.post(
        "/spaces/999/bookings", json={"member": "annabel", **slot(1, 2)}
    )

    #404 to reject booking a space that doesn't exist
    assert response.status_code == 404
    assert response.get_json() == {"error": "space not found"}

def test_create_booking_without_times_is_rejected():
    client = make_client()

    response = client.post("/spaces/1/bookings", json={"member": "annabel"})

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "start_time and end_time are required (ISO 8601 with timezone)"
    }

def test_create_booking_ending_before_it_starts_is_rejected():
    client = make_client()

    response = client.post(
        "/spaces/1/bookings", json={"member": "annabel", **slot(2, 1)}
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "end_time must be after start_time"}

def test_overlapping_booking_is_rejected():
    client = make_client()

    client.post("/spaces/1/bookings", json={"member": "annabel", **slot(1, 3)})
    response = client.post(
        "/spaces/1/bookings", json={"member": "gregory", **slot(2, 4)}
    )

    assert response.status_code == 409
    assert response.get_json() == {"error": "space is already booked for that time"}

def test_concurrent_bookings_for_the_same_slot_only_one_succeeds():
    # Two separate app instances = two separate real DB connections, like
    # two simultaneous requests would get in production. This is what
    # actually exercises the race (see issue #50) rather than just the
    # single-connection check-then-insert code path.
    client_a = make_client()
    client_b = create_app(reset_on_start=False).test_client()

    booking = {"member": "racer", **slot(1, 2)}
    results = [None, None]
    start_barrier = threading.Barrier(2)

    def book(client, index):
        start_barrier.wait()
        results[index] = client.post("/spaces/1/bookings", json=booking)

    threads = [
        threading.Thread(target=book, args=(client_a, 0)),
        threading.Thread(target=book, args=(client_b, 1)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    statuses = sorted(r.status_code for r in results)
    assert statuses == [201, 409]

def test_back_to_back_bookings_are_allowed():
    client = make_client()

    client.post("/spaces/1/bookings", json={"member": "annabel", **slot(1, 2)})
    response = client.post(
        "/spaces/1/bookings", json={"member": "gregory", **slot(2, 3)}
    )

    assert response.status_code == 201

def test_space_booked_right_now_shows_as_unavailable():
    client = make_client()

    client.post("/spaces/1/bookings", json={"member": "annabel", **slot(-1, 1)})
    response = client.get("/spaces")

    spaces = response.get_json()["spaces"]
    founders_desk = next(s for s in spaces if s["id"] == 1)
    assert founders_desk["available"] is False

def test_space_booked_only_later_is_still_available_now():
    client = make_client()

    client.post("/spaces/1/bookings", json={"member": "annabel", **slot(24, 25)})
    response = client.get("/spaces")

    spaces = response.get_json()["spaces"]
    founders_desk = next(s for s in spaces if s["id"] == 1)
    assert founders_desk["available"] is True

def test_find_booking_returns_the_booking():
    client = make_client()
    created = client.post(
        "/spaces/1/bookings", json={"member": "annabel", **slot(1, 2)}
    ).get_json()

    response = client.get(f"/bookings/{created['id']}")

    assert response.status_code == 200
    body = response.get_json()
    body.pop("created_at")
    assert body == {
        "id": 1,
        "space_id": 1,
        "member": "annabel",
        "paid": False,
        "amount_cents": 2500,
        "user_id": None,
        "card_last4": None,
        **slot(1, 2),
    }

def test_booking_records_when_it_was_made():
    client = make_client()

    # a second of slack: the database clock and this one are not identical
    before = datetime.now(timezone.utc) - timedelta(seconds=1)
    created = client.post(
        "/spaces/1/bookings", json={"member": "annabel", **slot(1, 2)}
    ).get_json()
    after = datetime.now(timezone.utc) + timedelta(seconds=1)

    created_at = datetime.fromisoformat(created["created_at"])
    assert before <= created_at <= after
    # it's when the booking was made, not when the space is used
    assert created_at < datetime.fromisoformat(created["start_time"])

    # and it survives being read back and paid
    assert client.get("/bookings/1").get_json()["created_at"] == created["created_at"]
    paid = client.post("/bookings/1/pay", json=VALID_CARD).get_json()
    assert paid["created_at"] == created["created_at"]

def test_find_unknown_booking_returns_404():
    client = make_client()

    response = client.get("/bookings/999")

    assert response.status_code == 404
    assert response.get_json() == {"error": "booking not found"}

def test_cancel_booking_makes_space_available_again():
    client = make_client()
    created = client.post(
        "/spaces/1/bookings", json={"member": "annabel", **slot(-1, 1)}
    ).get_json()

    response = client.delete(f"/bookings/{created['id']}")

    assert response.status_code == 200
    assert response.get_json() == created

    spaces = client.get("/spaces").get_json()["spaces"]
    founders_desk = next(s for s in spaces if s["id"] == 1)
    assert founders_desk["available"] is True

    assert client.get(f"/bookings/{created['id']}").status_code == 404

def test_cancel_unknown_booking_returns_404():
    client = make_client()

    response = client.delete("/bookings/999")

    assert response.status_code == 404
    assert response.get_json() == {"error": "booking not found"}

def test_unlock_before_paying_is_rejected():
    client = make_client()
    created = client.post(
        "/spaces/1/bookings", json={"member": "annabel", **slot(-1, 1)}
    ).get_json()

    response = client.post(f"/bookings/{created['id']}/unlock")

    assert response.status_code == 402
    assert response.get_json() == {"error": "booking is not paid"}

def test_unlock_a_paid_booking_returns_an_access_code():
    client = make_client()
    created = client.post(
        "/spaces/1/bookings", json={"member": "annabel", **slot(-1, 1)}
    ).get_json()
    client.post(f"/bookings/{created['id']}/pay", json=VALID_CARD)

    response = client.post(f"/bookings/{created['id']}/unlock")

    assert response.status_code == 200
    body = response.get_json()
    assert body["booking_id"] == created["id"]
    assert len(body["access_code"]) > 0

def test_unlocking_twice_returns_the_same_stored_code():
    client = make_client()
    client.post("/spaces/1/bookings", json={"member": "annabel", **slot(-1, 1)})
    client.post("/bookings/1/pay", json=VALID_CARD)

    first = client.post("/bookings/1/unlock").get_json()["access_code"]
    second = client.post("/bookings/1/unlock").get_json()["access_code"]

    assert first == second

def test_the_access_code_survives_a_restart():
    client = make_client()
    client.post("/spaces/1/bookings", json={"member": "annabel", **slot(-1, 1)})
    client.post("/bookings/1/pay", json=VALID_CARD)
    code = client.post("/bookings/1/unlock").get_json()["access_code"]

    # a second app instance, same database - as after a redeploy
    restarted = create_app(reset_on_start=False).test_client()

    assert restarted.post("/bookings/1/unlock").get_json()["access_code"] == code

def test_cancelling_a_booking_removes_its_access_code():
    client = make_client()
    client.post("/spaces/1/bookings", json={"member": "annabel", **slot(-1, 1)})
    client.post("/bookings/1/pay", json=VALID_CARD)
    client.post("/bookings/1/unlock")

    # the access row references the booking, so the delete has to cascade
    assert client.delete("/bookings/1").status_code == 200
    assert client.post("/bookings/1/unlock").status_code == 404

def test_unlock_unknown_booking_returns_404():
    client = make_client()

    response = client.post("/bookings/999/unlock")

    assert response.status_code == 404
    assert response.get_json() == {"error": "booking not found"}

def test_registering_creates_an_account():
    client = make_client()

    response = client.post(
        "/register", json={"email": "Annabel@Example.com", "password": "hunter22"}
    )

    assert response.status_code == 201
    body = response.get_json()
    assert body == {"id": 1, "email": "annabel@example.com"}
    assert "password" not in body
    assert "password_hash" not in body

def test_registering_does_not_store_the_plain_password():
    client = make_client()
    app = create_app(reset_on_start=False)
    client.post("/register", json={"email": "annabel@example.com", "password": "hunter22"})

    with app.db.cursor() as cur:
        cur.execute("SELECT password_hash FROM users WHERE email = 'annabel@example.com'")
        stored = cur.fetchone()["password_hash"]

    assert stored != "hunter22"
    assert "hunter22" not in stored

def test_registering_with_a_duplicate_email_is_rejected():
    client = make_client()
    client.post("/register", json={"email": "annabel@example.com", "password": "hunter22"})

    # case shouldn't matter either
    response = client.post(
        "/register", json={"email": "Annabel@Example.com", "password": "different"}
    )

    assert response.status_code == 409
    assert response.get_json() == {"error": "email is already registered"}

def test_registering_with_a_bad_email_is_rejected():
    client = make_client()

    for email in ["not-an-email", "missing-domain@", "@missing-local.com", "", None, 42]:
        response = client.post(
            "/register", json={"email": email, "password": "hunter22"}
        )

        assert response.status_code == 400
        assert response.get_json() == {"error": "enter a valid email address"}

def test_registering_with_a_short_password_is_rejected():
    client = make_client()

    for password in ["short", "", None, 12345678]:
        response = client.post(
            "/register", json={"email": "annabel@example.com", "password": password}
        )

        assert response.status_code == 400
        assert response.get_json() == {
            "error": "password must be at least 8 characters"
        }

    # nothing got created, so a later valid registration still succeeds
    created = client.post(
        "/register", json={"email": "annabel@example.com", "password": "hunter22"}
    )
    assert created.status_code == 201

def register(client, email="annabel@example.com", password="hunter22"):
    return client.post("/register", json={"email": email, "password": password})

def test_login_with_the_right_password_succeeds():
    client = make_client()
    register(client)

    response = client.post(
        "/login", json={"email": "Annabel@Example.com", "password": "hunter22"}
    )

    assert response.status_code == 200
    assert response.get_json() == {"id": 1, "email": "annabel@example.com"}

def test_login_with_the_wrong_password_is_rejected():
    client = make_client()
    register(client)

    response = client.post(
        "/login", json={"email": "annabel@example.com", "password": "wrong-password"}
    )

    assert response.status_code == 401
    assert response.get_json() == {"error": "invalid email or password"}

def test_login_with_an_unknown_email_gives_the_identical_error():
    client = make_client()
    register(client)

    known = client.post(
        "/login", json={"email": "annabel@example.com", "password": "wrong-password"}
    )
    unknown = client.post(
        "/login", json={"email": "nobody@example.com", "password": "hunter22"}
    )

    # a wrong password and an unregistered email must look the same,
    # otherwise a login attempt can be used to find out who has an account
    assert known.status_code == unknown.status_code == 401
    assert known.get_json() == unknown.get_json()

def test_logout_ends_the_session():
    client = make_client()
    register(client)
    client.post("/login", json={"email": "annabel@example.com", "password": "hunter22"})

    response = client.post("/logout")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}

    created = client.post(
        "/spaces/1/bookings", json={"member": "annabel", **slot(1, 2)}
    ).get_json()
    assert created["user_id"] is None

def test_logout_when_not_logged_in_is_a_no_op():
    client = make_client()

    response = client.post("/logout", json={})

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}

def login(client, email="annabel@example.com", password="hunter22"):
    return client.post("/login", json={"email": email, "password": password})

def test_a_booking_made_while_logged_in_is_linked_to_the_account():
    client = make_client()
    register(client)
    user = login(client).get_json()

    created = client.post(
        "/spaces/1/bookings", json={"member": "annabel", **slot(1, 2)}
    ).get_json()

    assert created["user_id"] == user["id"]
    assert client.get(f"/bookings/{created['id']}").get_json()["user_id"] == user["id"]

def test_a_guest_booking_has_no_user_id():
    client = make_client()

    created = client.post(
        "/spaces/1/bookings", json={"member": "annabel", **slot(1, 2)}
    ).get_json()

    assert created["user_id"] is None

def test_logging_out_before_booking_leaves_it_unlinked():
    client = make_client()
    register(client)
    login(client)
    client.post("/logout", json={})

    created = client.post(
        "/spaces/1/bookings", json={"member": "annabel", **slot(1, 2)}
    ).get_json()

    assert created["user_id"] is None

def test_user_id_stays_on_the_booking_through_pay_and_list():
    client = make_client()
    register(client)
    user = login(client).get_json()

    created = client.post(
        "/spaces/1/bookings", json={"member": "annabel", **slot(1, 2)}
    ).get_json()
    paid = client.post(f"/bookings/{created['id']}/pay", json=VALID_CARD).get_json()

    assert paid["user_id"] == user["id"]
    assert client.get("/bookings").get_json()["bookings"][0]["user_id"] == user["id"]
    assert (
        client.get("/spaces/1/bookings").get_json()["bookings"][0]["user_id"]
        == user["id"]
    )

def test_my_bookings_json_needs_a_login():
    client = make_client()

    response = client.get("/me/bookings")

    assert response.status_code == 401
    assert response.get_json() == {"error": "log in to see your bookings"}

def test_my_bookings_json_lists_only_the_logged_in_users_bookings():
    client = make_client()
    client.post("/spaces/1/bookings", json={"member": "guest", **slot(1, 2)})
    register(client, email="annabel@example.com")
    register(client, email="gregory@example.com")

    login(client, email="annabel@example.com")
    client.post("/spaces/1/bookings", json={"member": "annabel", **slot(3, 4)})
    client.post("/logout", json={})

    login(client, email="gregory@example.com")
    mine = client.post(
        "/spaces/1/bookings", json={"member": "gregory", **slot(5, 6)}
    ).get_json()

    response = client.get("/me/bookings")

    assert response.status_code == 200
    assert response.get_json() == {"bookings": [mine]}

def test_booking_member_is_trimmed_and_blank_means_guest():
    client = make_client()

    trimmed = client.post(
        "/spaces/1/bookings", json={"member": "  member b ", **slot(1, 2)}
    ).get_json()
    blank = client.post(
        "/spaces/1/bookings", json={"member": "   ", **slot(3, 4)}
    ).get_json()

    assert trimmed["member"] == "member b"
    assert blank["member"] == "guest"

def test_booking_member_must_be_a_string():
    client = make_client()

    response = client.post("/spaces/1/bookings", json={"member": 5, **slot(1, 2)})

    assert response.status_code == 400
    assert response.get_json() == {"error": "member must be a string"}

def test_subscribing_returns_an_active_subscription():
    client = make_client()

    response = client.post("/members/annabel/subscribe")

    assert response.status_code == 200
    body = response.get_json()
    assert body["member"] == "annabel"
    assert body["active"] is True
    assert body["started_at"]

def test_subscribing_twice_keeps_the_original_start():
    client = make_client()
    first = client.post("/members/annabel/subscribe").get_json()

    second = client.post("/members/annabel/subscribe")

    assert second.status_code == 200
    assert second.get_json() == first

def test_a_blank_member_name_cannot_subscribe():
    client = make_client()

    response = client.post("/members/%20/subscribe")

    assert response.status_code == 400
    assert response.get_json() == {"error": "member name must not be blank"}

def test_a_subscribed_members_booking_is_paid_on_creation():
    client = make_client()
    client.post("/members/annabel/subscribe")

    created = client.post(
        "/spaces/1/bookings", json={"member": "annabel", **slot(-1, 1)}
    ).get_json()

    assert created["paid"] is True
    # no pay step needed to get the access code
    unlock = client.post(f"/bookings/{created['id']}/unlock")
    assert unlock.status_code == 200

def test_a_member_without_a_subscription_still_pays_once():
    client = make_client()
    client.post("/members/annabel/subscribe")

    created = client.post(
        "/spaces/1/bookings", json={"member": "gregory", **slot(1, 2)}
    ).get_json()

    assert created["paid"] is False
    paid = client.post(f"/bookings/{created['id']}/pay", json=VALID_CARD).get_json()
    assert paid["paid"] is True

def test_subscription_matches_the_member_name_ignoring_case_and_spaces():
    client = make_client()
    client.post("/members/%20Annabel%20/subscribe")

    lower = client.post(
        "/spaces/1/bookings", json={"member": "annabel", **slot(1, 2)}
    ).get_json()
    shouting = client.post(
        "/spaces/1/bookings", json={"member": "  ANNABEL ", **slot(3, 4)}
    ).get_json()

    assert lower["paid"] is True
    assert shouting["paid"] is True

def test_a_subscribers_booking_adds_no_per_booking_revenue():
    client = make_client()
    client.post(
        "/spaces",
        json={"name": "Meeting Room A", "capacity": 6, "price_cents": 1500},
    )
    client.post("/members/annabel/subscribe")

    client.post("/spaces/2/bookings", json={"member": "annabel", **slot(1, 2)})
    metrics = client.get("/metrics").get_json()

    assert metrics["paid_bookings"] == 1
    assert metrics["revenue_cents"] == 0

def test_metrics_reports_spaces_bookings_and_members():
    client = make_client()

    client.post("/spaces", json={"name": "Meeting Room A", "capacity": 6})
    client.post("/spaces/1/bookings", json={"member": "annabel", **slot(1, 2)})
    client.post("/spaces/2/bookings", json={"member": "gregory", **slot(1, 2)})

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
    first = client.post(
        "/spaces/1/bookings", json={"member": "annabel", **slot(1, 2)}
    ).get_json()
    client.post("/spaces/1/bookings", json={"member": "gregory", **slot(3, 4)})
    client.post("/spaces/1/bookings", json={"member": "flurina", **slot(5, 6)})
    client.post(f"/bookings/{first['id']}/pay", json=VALID_CARD)

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

    client.post(
        "/spaces",
        json={"name": "Meeting Room A", "capacity": 6, "price_cents": 1500},
    )
    booking = client.post(
        "/spaces/2/bookings", json={"member": "gregory", **slot(1, 2)}
    ).get_json()

    # unpaid bookings bring in nothing yet
    assert client.get("/metrics").get_json()["revenue_cents"] == 0

    client.post(f"/bookings/{booking['id']}/pay", json=VALID_CARD)
    response = client.get("/metrics")

    assert response.status_code == 200
    body = response.get_json()
    assert body["revenue_cents"] == 1500

def test_utilization_over_the_next_7_days():
    client = make_client()
    # Founders Desk (the only space) is booked for a clean 24 hours,
    # fully inside the 7-day (168h) window.
    client.post("/spaces/1/bookings", json={"member": "annabel", **slot(1, 25)})

    body = client.get("/metrics").get_json()

    assert body["utilization"] == pytest.approx(24 / 168)

def test_utilization_ignores_bookings_outside_the_next_7_days():
    client = make_client()
    client.post("/spaces/1/bookings", json={"member": "annabel", **slot(200, 201)})

    body = client.get("/metrics").get_json()

    assert body["utilization"] == 0.0

def test_utilization_is_zero_with_no_spaces():
    client = make_client()
    client.delete("/spaces/1")  # the only space, unbooked, so deletable

    body = client.get("/metrics").get_json()

    assert body["spaces"] == 0
    assert body["utilization"] == 0.0

def test_repeat_member_rate():
    client = make_client()
    client.post("/spaces", json={"name": "Room B", "capacity": 4})
    client.post("/spaces/1/bookings", json={"member": "annabel", **slot(1, 2)})
    client.post("/spaces/2/bookings", json={"member": "annabel", **slot(3, 4)})
    client.post("/spaces/2/bookings", json={"member": "gregory", **slot(5, 6)})

    body = client.get("/metrics").get_json()

    assert body["members"] == 2
    assert body["repeat_member_rate"] == pytest.approx(1 / 2)  # only annabel repeats

def test_repeat_member_rate_is_zero_with_no_bookings():
    client = make_client()

    body = client.get("/metrics").get_json()

    assert body["repeat_member_rate"] == 0.0

def test_payment_conversion():
    client = make_client()
    first = client.post(
        "/spaces/1/bookings", json={"member": "annabel", **slot(1, 2)}
    ).get_json()
    client.post("/spaces/1/bookings", json={"member": "gregory", **slot(3, 4)})
    client.post(f"/bookings/{first['id']}/pay", json=VALID_CARD)

    body = client.get("/metrics").get_json()

    assert body["payment_conversion"] == pytest.approx(0.5)

def test_payment_conversion_is_zero_with_no_bookings():
    client = make_client()

    body = client.get("/metrics").get_json()

    assert body["payment_conversion"] == 0.0

def test_average_revenue_per_paid_booking():
    client = make_client()
    client.post(
        "/spaces", json={"name": "Room B", "capacity": 4, "price_cents": 1000}
    )
    first = client.post(
        "/spaces/2/bookings", json={"member": "annabel", **slot(1, 2)}
    ).get_json()
    second = client.post(
        "/spaces/2/bookings", json={"member": "gregory", **slot(3, 4)}
    ).get_json()
    client.post(f"/bookings/{first['id']}/pay", json=VALID_CARD)
    client.post(f"/bookings/{second['id']}/pay", json=VALID_CARD)

    body = client.get("/metrics").get_json()

    assert body["avg_revenue_cents_per_paid_booking"] == 1000

def test_average_revenue_per_paid_booking_is_zero_with_no_paid_bookings():
    client = make_client()

    body = client.get("/metrics").get_json()

    assert body["avg_revenue_cents_per_paid_booking"] == 0

def test_revenue_by_space():
    client = make_client()
    client.post(
        "/spaces", json={"name": "Room B", "capacity": 4, "price_cents": 1000}
    )
    client.post("/spaces/1/bookings", json={"member": "annabel", **slot(1, 2)})
    paid = client.post(
        "/spaces/2/bookings", json={"member": "gregory", **slot(3, 4)}
    ).get_json()
    client.post(f"/bookings/{paid['id']}/pay", json=VALID_CARD)

    body = client.get("/metrics").get_json()

    by_id = {row["id"]: row for row in body["revenue_by_space"]}
    assert by_id[1]["name"] == "Founders Desk"
    assert by_id[1]["revenue_cents"] == 0  # unpaid
    assert by_id[2]["name"] == "Room B"
    assert by_id[2]["revenue_cents"] == 1000

def test_revenue_by_space_includes_a_space_with_no_bookings():
    client = make_client()

    body = client.get("/metrics").get_json()

    assert body["revenue_by_space"] == [
        {"id": 1, "name": "Founders Desk", "revenue_cents": 0}
    ]

def test_revenue_stays_the_same_when_the_space_price_changes_later():
    app = create_app(reset_on_start=True)
    client = app.test_client()
    client.post(
        "/spaces",
        json={"name": "Meeting Room A", "capacity": 6, "price_cents": 500},
    )
    booking = client.post(
        "/spaces/2/bookings", json={"member": "gregory", **slot(1, 2)}
    ).get_json()
    client.post(f"/bookings/{booking['id']}/pay", json=VALID_CARD)
    assert client.get("/metrics").get_json()["revenue_cents"] == 500

    # PATCH can't change a price yet, so change it straight in the database
    with app.db.cursor() as cur:
        cur.execute("UPDATE spaces SET price_cents = 2000 WHERE id = 2")

    assert client.get("/metrics").get_json()["revenue_cents"] == 500

    # a booking made after the price change is charged the new price
    later = client.post(
        "/spaces/2/bookings", json={"member": "annabel", **slot(3, 4)}
    ).get_json()
    client.post(f"/bookings/{later['id']}/pay", json=VALID_CARD)
    assert client.get("/metrics").get_json()["revenue_cents"] == 2500

def test_startup_fills_in_the_amount_on_bookings_made_before_the_column_existed():
    app = create_app(reset_on_start=True)
    client = app.test_client()
    client.post(
        "/spaces",
        json={"name": "Meeting Room A", "capacity": 6, "price_cents": 1500},
    )
    with app.db.cursor() as cur:
        # a paid booking from before amount_cents existed, so it has no amount
        cur.execute(
            "INSERT INTO bookings (space_id, member, paid, start_time, end_time) "
            "VALUES (2, 'old-member', TRUE, "
            "now() + interval '1 day', now() + interval '2 days')"
        )
        cur.execute("SELECT amount_cents FROM bookings")
        assert cur.fetchone()["amount_cents"] is None

    create_app(reset_on_start=False)  # the next app start runs the backfill

    with app.db.cursor() as cur:
        cur.execute("SELECT amount_cents FROM bookings")
        assert cur.fetchone()["amount_cents"] == 1500
    assert client.get("/metrics").get_json()["revenue_cents"] == 1500

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

def test_delete_unbooked_space_removes_it():
    client = make_client()
    created = client.post(
        "/spaces", json={"name": "Temp Room", "capacity": 3}
    ).get_json()

    response = client.delete(f"/spaces/{created['id']}")

    assert response.status_code == 204
    listed = client.get("/spaces").get_json()["spaces"]
    assert all(s["id"] != created["id"] for s in listed)

def test_delete_space_with_bookings_is_rejected():
    client = make_client()
    client.post("/spaces/1/bookings", json={"member": "annabel", **slot(1, 2)})

    response = client.delete("/spaces/1")

    assert response.status_code == 409
    assert response.get_json() == {
        "error": "space has bookings, cancel them first"
    }

def test_delete_unknown_space_returns_404():
    client = make_client()

    response = client.delete("/spaces/999")

    assert response.status_code == 404
    assert response.get_json() == {"error": "space not found"}

def test_get_space_returns_its_data():
    client = make_client()

    response = client.get("/spaces/1")

    assert response.status_code == 200
    assert response.get_json() == {
        "id": 1,
        "name": "Founders Desk",
        "capacity": 1,
        "price_cents": 2500,
        "available": True,
    }

def test_get_unknown_space_returns_404():
    client = make_client()

    response = client.get("/spaces/999")

    assert response.status_code == 404
    assert response.get_json() == {"error": "space not found"}

def test_list_spaces_for_a_time_window_reflects_bookings_in_that_window():
    client = make_client()
    client.post("/spaces/1/bookings", json={"member": "annabel", **slot(48, 50)})

    # free right now, so the default view says available
    assert client.get("/spaces").get_json()["spaces"][0]["available"] is True

    clash = client.get("/spaces", query_string=slot(47, 49)).get_json()
    free = client.get("/spaces", query_string=slot(60, 61)).get_json()

    assert clash["spaces"][0]["available"] is False
    assert free["spaces"][0]["available"] is True

def test_availability_window_replaces_the_right_now_check():
    client = make_client()
    client.post("/spaces/1/bookings", json={"member": "annabel", **slot(-1, 1)})

    assert client.get("/spaces").get_json()["spaces"][0]["available"] is False

    later = client.get("/spaces", query_string=slot(5, 6)).get_json()

    assert later["spaces"][0]["available"] is True

def test_availability_window_touching_a_booking_is_still_free():
    client = make_client()
    client.post("/spaces/1/bookings", json={"member": "annabel", **slot(-1, 1)})
    client.post("/spaces/1/bookings", json={"member": "gregory", **slot(3, 4)})

    # starts exactly when the first booking ends
    after = client.get("/spaces", query_string=slot(1, 2)).get_json()
    # ends exactly when the second booking starts
    before = client.get("/spaces", query_string=slot(2, 3)).get_json()

    assert after["spaces"][0]["available"] is True
    assert before["spaces"][0]["available"] is True

def test_get_space_for_a_time_window_reflects_bookings_in_that_window():
    client = make_client()
    client.post("/spaces/1/bookings", json={"member": "annabel", **slot(48, 50)})

    clash = client.get("/spaces/1", query_string=slot(49, 51))
    free = client.get("/spaces/1", query_string=slot(60, 61))

    assert clash.status_code == 200
    assert clash.get_json()["available"] is False
    assert free.get_json()["available"] is True

def test_availability_window_must_have_both_times_in_order():
    client = make_client()
    together = (
        "start_time and end_time must be given together "
        "(ISO 8601 with timezone, e.g. 2026-09-25T09:00:00Z)"
    )
    bad_windows = [
        ({"start_time": slot(1, 2)["start_time"]}, together),
        ({"start_time": "tomorrow", "end_time": "later"}, together),
        (
            {"start_time": "2026-09-25T09:00:00", "end_time": "2026-09-25T10:00:00"},
            together,  # no timezone
        ),
        (slot(2, 1), "end_time must be after start_time"),
    ]

    for path in ["/spaces", "/spaces/1"]:
        for query, message in bad_windows:
            response = client.get(path, query_string=query)

            assert response.status_code == 400
            assert response.get_json() == {"error": message}

def test_list_bookings_for_a_space_returns_all_of_them():
    client = make_client()
    client.post("/spaces", json={"name": "Meeting Room A", "capacity": 6})
    later = client.post(
        "/spaces/1/bookings", json={"member": "gregory", **slot(3, 4)}
    ).get_json()
    earlier = client.post(
        "/spaces/1/bookings", json={"member": "annabel", **slot(1, 2)}
    ).get_json()
    client.post("/spaces/2/bookings", json={"member": "flurina", **slot(1, 2)})

    response = client.get("/spaces/1/bookings")

    # only space 1's bookings, earliest first
    assert response.status_code == 200
    assert response.get_json() == {"bookings": [earlier, later]}

def test_list_bookings_for_space_without_bookings_is_empty():
    client = make_client()

    response = client.get("/spaces/1/bookings")

    assert response.status_code == 200
    assert response.get_json() == {"bookings": []}

def test_list_bookings_for_unknown_space_returns_404():
    client = make_client()

    response = client.get("/spaces/999/bookings")

    assert response.status_code == 404
    assert response.get_json() == {"error": "space not found"}

def test_booking_more_people_than_capacity_is_rejected():
    client = make_client()

    # Founders Desk (space 1) has capacity 1
    response = client.post(
        "/spaces/1/bookings",
        json={"member": "annabel", "party_size": 2, **slot(1, 2)},
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "party_size 2 exceeds this space's capacity of 1"
    }
    assert client.get("/spaces/1/bookings").get_json() == {"bookings": []}

def test_booking_up_to_capacity_is_allowed():
    client = make_client()
    client.post("/spaces", json={"name": "Meeting Room A", "capacity": 6})

    response = client.post(
        "/spaces/2/bookings",
        json={"member": "annabel", "party_size": 6, **slot(1, 2)},
    )

    assert response.status_code == 201

def test_booking_with_invalid_party_size_is_rejected():
    client = make_client()

    for party_size in [0, -3, "two", 1.5, True]:
        response = client.post(
            "/spaces/1/bookings",
            json={"member": "annabel", "party_size": party_size, **slot(1, 2)},
        )

        assert response.status_code == 400
        assert response.get_json() == {
            "error": "party_size must be a whole number of at least 1"
        }
def test_list_bookings_returns_bookings_across_all_spaces():
    client = make_client()
    client.post("/spaces", json={"name": "Meeting Room A", "capacity": 6})
    later = client.post(
        "/spaces/1/bookings", json={"member": "gregory", **slot(3, 4)}
    ).get_json()
    earlier = client.post(
        "/spaces/2/bookings", json={"member": "annabel", **slot(1, 2)}
    ).get_json()

    response = client.get("/bookings")

    # bookings from both spaces, earliest first
    assert response.status_code == 200
    assert response.get_json() == {"bookings": [earlier, later]}

def test_list_bookings_when_there_are_none_is_empty():
    client = make_client()

    response = client.get("/bookings")

    assert response.status_code == 200
    assert response.get_json() == {"bookings": []}

def test_update_space_name_shows_up_in_list():
    client = make_client()

    response = client.patch("/spaces/1", json={"name": "Founders Desk (Window)"})

    assert response.status_code == 200
    assert response.get_json() == {
        "id": 1,
        "name": "Founders Desk (Window)",
        "capacity": 1,
        "price_cents": 2500,
    }
    spaces = client.get("/spaces").get_json()["spaces"]
    assert spaces[0]["name"] == "Founders Desk (Window)"
    assert spaces[0]["capacity"] == 1  # untouched

def test_update_space_capacity_only_keeps_the_name():
    client = make_client()

    response = client.patch("/spaces/1", json={"capacity": 4})

    assert response.status_code == 200
    assert response.get_json()["name"] == "Founders Desk"
    assert response.get_json()["capacity"] == 4

def test_update_space_keeps_its_bookings():
    client = make_client()
    booking = client.post(
        "/spaces/1/bookings", json={"member": "annabel", **slot(1, 2)}
    ).get_json()

    client.patch("/spaces/1", json={"name": "Renamed Desk"})

    assert client.get("/spaces/1/bookings").get_json() == {"bookings": [booking]}

def test_update_space_price_shows_up_in_list():
    client = make_client()
    client.post(
        "/spaces",
        json={"name": "Meeting Room A", "capacity": 6, "price_cents": 500},
    )

    response = client.patch("/spaces/2", json={"price_cents": 2000})

    assert response.status_code == 200
    assert response.get_json() == {
        "id": 2,
        "name": "Meeting Room A",
        "capacity": 6,
        "price_cents": 2000,
    }
    listed = client.get("/spaces").get_json()["spaces"]
    assert next(s for s in listed if s["id"] == 2)["price_cents"] == 2000

    # 0 is a valid price: the space becomes free
    free = client.patch("/spaces/2", json={"price_cents": 0})
    assert free.get_json()["price_cents"] == 0

def test_update_space_name_only_keeps_the_price():
    client = make_client()
    client.post(
        "/spaces",
        json={"name": "Meeting Room A", "capacity": 6, "price_cents": 500},
    )

    response = client.patch("/spaces/2", json={"name": "Meeting Room B"})

    assert response.get_json()["price_cents"] == 500

def test_changing_a_price_with_patch_only_affects_later_bookings():
    client = make_client()
    client.post(
        "/spaces",
        json={"name": "Meeting Room A", "capacity": 6, "price_cents": 500},
    )
    before = client.post(
        "/spaces/2/bookings", json={"member": "gregory", **slot(1, 2)}
    ).get_json()
    client.post(f"/bookings/{before['id']}/pay", json=VALID_CARD)
    assert client.get("/metrics").get_json()["revenue_cents"] == 500

    client.patch("/spaces/2", json={"price_cents": 2000})

    # the booking made before the change keeps what it was charged
    assert client.get("/metrics").get_json()["revenue_cents"] == 500

    after = client.post(
        "/spaces/2/bookings", json={"member": "annabel", **slot(3, 4)}
    ).get_json()
    client.post(f"/bookings/{after['id']}/pay", json=VALID_CARD)
    assert client.get("/metrics").get_json()["revenue_cents"] == 2500

def test_update_unknown_space_returns_404():
    client = make_client()

    response = client.patch("/spaces/999", json={"name": "Ghost Room"})

    assert response.status_code == 404
    assert response.get_json() == {"error": "space not found"}

def test_update_space_with_invalid_values_is_rejected():
    client = make_client()

    price_error = "price_cents must be a non-negative integer"
    cases = [
        ({}, "provide name, capacity and/or price_cents to update"),
        ({"name": "   "}, "name must not be empty"),
        ({"name": None}, "name must not be empty"),
        ({"capacity": 0}, "capacity must be a whole number of at least 1"),
        ({"capacity": "4"}, "capacity must be a whole number of at least 1"),
        ({"price_cents": -1}, price_error),
        ({"price_cents": "5"}, price_error),
        ({"price_cents": 2.5}, price_error),
        ({"price_cents": None}, price_error),
        ({"price_cents": True}, price_error),
    ]
    for body, error in cases:
        response = client.patch("/spaces/1", json=body)

        assert response.status_code == 400
        assert response.get_json() == {"error": error}

    # nothing changed
    space = client.get("/spaces/1").get_json()
    assert space["name"] == "Founders Desk"
    assert space["price_cents"] == 2500


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
