import threading
from datetime import UTC, datetime, timedelta

import pytest
from purchase_helpers import BOOKING_KEYS, login, make_client, pay, register, slot

from app import create_app

# The service's routes. PUR-018, PUR-019 and PUR-020 add theirs here.
CONTRACT = {
    "GET /spaces",
    "POST /spaces",
    "GET /metrics",
    "GET /spaces/booked-ids",
    "GET /spaces/<int:space_id>",
    "PATCH /spaces/<int:space_id>",
    "DELETE /spaces/<int:space_id>",
    "GET /spaces/<int:space_id>/bookings",
    "POST /spaces/<int:space_id>/bookings",
    "GET /bookings",
    "GET /bookings/<int:booking_id>",
    "GET /bookings/<int:booking_id>/payable",
    "POST /bookings/<int:booking_id>/paid",
    "DELETE /bookings/<int:booking_id>",
    "GET /bookings/<int:booking_id>/unlockable",
    "GET /bookings/<int:booking_id>/cancellable",
    "POST /register",
    "POST /users/authenticate",
    "GET /users/<int:user_id>/bookings",
}

# One request per PUR-016 route, against space 1 or 2, booking 1, user 1.
# Routes added later join CONTRACT only and test their own token.
ROUTES = [
    ("GET", "/spaces", None),
    ("POST", "/spaces", {"name": "Meeting Room A", "capacity": 6}),
    ("GET", "/spaces/booked-ids", None),
    ("GET", "/spaces/1", None),
    ("PATCH", "/spaces/1", {"name": "Renamed"}),
    ("DELETE", "/spaces/2", None),
    ("GET", "/spaces/1/bookings", None),
    ("POST", "/spaces/1/bookings", {"member": "gregory", **slot(3, 4)}),
    ("GET", "/bookings", None),
    ("GET", "/bookings/1", None),
    ("DELETE", "/bookings/1", None),
    ("POST", "/register", {"email": "gregory@example.com", "password": "hunter22"}),
    (
        "POST",
        "/users/authenticate",
        {"email": "annabel@example.com", "password": "hunter22"},
    ),
    ("GET", "/users/1/bookings", None),
]


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
    client.post(
        "/spaces", json={"name": "Meeting Room A", "capacity": 6, "price_cents": 1500}
    )

    created = client.post(
        "/spaces/2/bookings", json={"member": "annabel", **slot(1, 2)}
    ).get_json()
    assert created["amount_cents"] == 1500

    # every other place a booking shows up agrees with what was charged
    assert client.get(f"/bookings/{created['id']}").get_json()["amount_cents"] == 1500
    assert client.get("/bookings").get_json()["bookings"][0]["amount_cents"] == 1500
    assert (
        client.get("/spaces/2/bookings").get_json()["bookings"][0]["amount_cents"]
        == 1500
    )
    paid = pay(client, created["id"])
    assert paid["amount_cents"] == 1500
    cancelled = client.delete(f"/bookings/{created['id']}").get_json()
    assert cancelled["amount_cents"] == 1500


def test_a_price_change_after_booking_does_not_change_the_amount_shown():
    client = make_client()
    client.post(
        "/spaces", json={"name": "Meeting Room A", "capacity": 6, "price_cents": 500}
    )
    created = client.post(
        "/spaces/2/bookings", json={"member": "annabel", **slot(1, 2)}
    ).get_json()

    client.patch("/spaces/2", json={"price_cents": 2000})

    assert client.get(f"/bookings/{created['id']}").get_json()["amount_cents"] == 500


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
                "available": True,
            }
        ]
    }


def test_create_space_adds_a_new_space():
    client = make_client()

    response = client.post("/spaces", json={"name": "Meeting Room A", "capacity": 6})

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

    # 404 to reject booking a space that doesn't exist
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
    client_b = make_client(reset=False)

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
    before = datetime.now(UTC) - timedelta(seconds=1)
    created = client.post(
        "/spaces/1/bookings", json={"member": "annabel", **slot(1, 2)}
    ).get_json()
    after = datetime.now(UTC) + timedelta(seconds=1)

    created_at = datetime.fromisoformat(created["created_at"])
    assert before <= created_at <= after
    # it's when the booking was made, not when the space is used
    assert created_at < datetime.fromisoformat(created["start_time"])

    # and it survives being read back and paid
    assert client.get("/bookings/1").get_json()["created_at"] == created["created_at"]
    paid = pay(client, 1)
    assert paid["created_at"] == created["created_at"]


def test_find_unknown_booking_returns_404():
    client = make_client()

    response = client.get("/bookings/999")

    assert response.status_code == 404
    assert response.get_json() == {"error": "booking not found"}


def test_cancel_booking_makes_space_available_again():
    client = make_client()
    created = client.post(
        "/spaces/1/bookings", json={"member": "annabel", **slot(1, 2)}
    ).get_json()

    response = client.delete(f"/bookings/{created['id']}")

    assert response.status_code == 200
    assert response.get_json() == created

    spaces = client.get("/spaces", query_string=slot(1, 2)).get_json()["spaces"]
    founders_desk = next(s for s in spaces if s["id"] == 1)
    assert founders_desk["available"] is True

    assert client.get(f"/bookings/{created['id']}").status_code == 404


def test_cancel_unknown_booking_returns_404():
    client = make_client()

    response = client.delete("/bookings/999")

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
    app = create_app()
    client.post(
        "/register", json={"email": "annabel@example.com", "password": "hunter22"}
    )

    with app.db.cursor() as cur:
        cur.execute(
            "SELECT password_hash FROM users WHERE email = 'annabel@example.com'"
        )
        stored = cur.fetchone()["password_hash"]

    assert stored != "hunter22"
    assert "hunter22" not in stored


def test_registering_with_a_duplicate_email_is_rejected():
    client = make_client()
    client.post(
        "/register", json={"email": "annabel@example.com", "password": "hunter22"}
    )

    # case shouldn't matter either
    response = client.post(
        "/register", json={"email": "Annabel@Example.com", "password": "different"}
    )

    assert response.status_code == 409
    assert response.get_json() == {"error": "email is already registered"}


def test_registering_with_a_bad_email_is_rejected():
    client = make_client()

    for email in [
        "not-an-email",
        "missing-domain@",
        "@missing-local.com",
        "",
        None,
        42,
    ]:
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


def test_login_with_the_right_password_succeeds():
    client = make_client()
    register(client)

    response = client.post(
        "/users/authenticate",
        json={"email": "Annabel@Example.com", "password": "hunter22"},
    )

    assert response.status_code == 200
    assert response.get_json() == {"id": 1, "email": "annabel@example.com"}


def test_login_with_the_wrong_password_is_rejected():
    client = make_client()
    register(client)

    response = client.post(
        "/users/authenticate",
        json={"email": "annabel@example.com", "password": "wrong-password"},
    )

    assert response.status_code == 401
    assert response.get_json() == {"error": "invalid email or password"}


def test_login_with_an_unknown_email_gives_the_identical_error():
    client = make_client()
    register(client)

    known = client.post(
        "/users/authenticate",
        json={"email": "annabel@example.com", "password": "wrong-password"},
    )
    unknown = client.post(
        "/users/authenticate",
        json={"email": "nobody@example.com", "password": "hunter22"},
    )

    # a wrong password and an unregistered email must look the same,
    # otherwise a login attempt can be used to find out who has an account
    assert known.status_code == unknown.status_code == 401
    assert known.get_json() == unknown.get_json()


def test_a_booking_made_while_logged_in_is_linked_to_the_account():
    client = make_client()
    register(client)
    user = login(client).get_json()

    created = client.post(
        "/spaces/1/bookings",
        json={"member": "annabel", "user_id": user["id"], **slot(1, 2)},
    ).get_json()

    assert created["user_id"] == user["id"]
    assert client.get(f"/bookings/{created['id']}").get_json()["user_id"] == user["id"]


def test_a_guest_booking_has_no_user_id():
    client = make_client()

    created = client.post(
        "/spaces/1/bookings", json={"member": "annabel", **slot(1, 2)}
    ).get_json()

    assert created["user_id"] is None


def test_user_id_stays_on_the_booking_through_pay_and_list():
    client = make_client()
    register(client)
    user = login(client).get_json()

    created = client.post(
        "/spaces/1/bookings",
        json={"member": "annabel", "user_id": user["id"], **slot(1, 2)},
    ).get_json()
    paid = pay(client, created["id"])

    assert paid["user_id"] == user["id"]
    assert client.get("/bookings").get_json()["bookings"][0]["user_id"] == user["id"]
    assert (
        client.get("/spaces/1/bookings").get_json()["bookings"][0]["user_id"]
        == user["id"]
    )


def test_my_bookings_json_lists_only_the_logged_in_users_bookings():
    client = make_client()
    client.post("/spaces/1/bookings", json={"member": "guest", **slot(1, 2)})
    register(client, email="annabel@example.com")
    register(client, email="gregory@example.com")

    annabel = login(client, email="annabel@example.com").get_json()
    client.post(
        "/spaces/1/bookings",
        json={"member": "annabel", "user_id": annabel["id"], **slot(3, 4)},
    )

    gregory = login(client, email="gregory@example.com").get_json()
    mine = client.post(
        "/spaces/1/bookings",
        json={"member": "gregory", "user_id": gregory["id"], **slot(5, 6)},
    ).get_json()

    response = client.get(f"/users/{gregory['id']}/bookings")

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
    assert response.get_json() == {"error": "space has bookings, cancel them first"}


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


def snapshot(app):
    """Every Purchase table's rows, to show a request changed nothing."""
    return {
        table: app.db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
        for table in ("users", "spaces", "bookings", "subscriptions")
    }


def stored_status(app, booking_id):
    row = app.db.execute("SELECT status FROM bookings WHERE id = %s", (booking_id,))
    return row.fetchone()["status"]


def test_route_table_is_the_contract():
    app = create_app()
    adapter = app.url_map.bind("localhost")

    routes = {
        f"{method} {rule.rule}"
        for rule in app.url_map.iter_rules()
        if rule.endpoint != "static"
        for method in rule.methods - {"HEAD", "OPTIONS"}
    }
    requested = {
        f"{method} {adapter.match(path, method=method, return_rule=True)[0].rule}"
        for method, path, _ in ROUTES
    }

    assert routes == CONTRACT | {"GET /health", "POST /test/reset"}
    assert requested <= CONTRACT


@pytest.mark.parametrize(
    ("method", "path", "body"), ROUTES, ids=[f"{m} {p}" for m, p, _ in ROUTES]
)
def test_every_route_refuses_a_missing_token(method, path, body):
    client = make_client()
    client.post("/spaces", json={"name": "Spare Room", "capacity": 2})
    user = register(client).get_json()
    client.post(
        "/spaces/1/bookings",
        json={"member": "annabel", "user_id": user["id"], **slot(1, 2)},
    )
    before = snapshot(client.application)
    without_token = client.application.test_client()

    response = without_token.open(path, method=method, json=body)

    assert response.status_code == 403
    assert response.get_json() == {"error": "invalid service token"}
    assert snapshot(client.application) == before


def test_authenticate_starts_no_session():
    client = make_client()
    register(client)

    response = login(client)

    assert response.status_code == 200
    assert "Set-Cookie" not in response.headers


def test_user_bookings_of_an_unknown_user_is_empty():
    client = make_client()

    response = client.get("/users/999/bookings")

    assert response.status_code == 200
    assert response.get_json() == {"bookings": []}


WINDOW_ERROR = (
    "start_time and end_time must be given together "
    "(ISO 8601 with timezone, e.g. 2026-09-25T09:00:00Z)"
)


@pytest.mark.parametrize(
    ("query", "status", "body"),
    [
        ({}, 200, {"space_ids": [1]}),
        (slot(0, 2), 200, {"space_ids": [1]}),
        (slot(1, 2), 200, {"space_ids": []}),
        ({"start_time": slot(1, 2)["start_time"]}, 400, {"error": WINDOW_ERROR}),
        (
            {"start_time": "2030-01-01T10:00:00", "end_time": "2030-01-01T11:00:00"},
            400,
            {"error": WINDOW_ERROR},
        ),
        (slot(1, 1), 400, {"error": "end_time must be after start_time"}),
    ],
    ids=["now", "overlapping", "touching", "start_only", "naive", "end_not_after"],
)
def test_booked_ids_examples(query, status, body):
    client = make_client()
    client.post("/spaces", json={"name": "Meeting Room A", "capacity": 6})
    client.post("/spaces/1/bookings", json={"member": "annabel", **slot(-1, 1)})

    response = client.get("/spaces/booked-ids", query_string=query)

    assert response.status_code == status
    assert response.get_json() == body


@pytest.mark.parametrize(
    "user_id", ["1", True, 1.5, {}], ids=['"1"', "true", "1.5", "{}"]
)
def test_create_booking_rejects_a_bad_user_id(user_id):
    client = make_client()
    register(client)

    response = client.post(
        "/spaces/1/bookings",
        json={"member": "annabel", "user_id": user_id, **slot(1, 2)},
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "user_id must be a whole number or null"}
    assert client.get("/bookings").get_json() == {"bookings": []}


@pytest.mark.parametrize("user_id", [999, 2**40, 2**70], ids=["999", "2**40", "2**70"])
def test_create_booking_rejects_an_unknown_user_id(user_id):
    client = make_client()
    register(client)

    response = client.post(
        "/spaces/1/bookings",
        json={"member": "annabel", "user_id": user_id, **slot(1, 2)},
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "user_id is not a known account"}
    assert client.get("/bookings").get_json() == {"bookings": []}


@pytest.mark.parametrize("body", [42, True, "x", [1]], ids=["42", "true", '"x"', "[1]"])
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/spaces"),
        ("PATCH", "/spaces/1"),
        ("POST", "/spaces/1/bookings"),
        ("POST", "/register"),
        ("POST", "/users/authenticate"),
    ],
    ids=["create_space", "update_space", "create_booking", "register", "authenticate"],
)
def test_non_object_json_body_counts_as_empty(method, path, body):
    client = make_client()
    before = snapshot(client.application)
    empty = client.open(path, method=method, json={})

    response = client.open(path, method=method, json=body)

    assert (response.status_code, response.get_json()) == (
        empty.status_code,
        empty.get_json(),
    )
    assert snapshot(client.application) == before


def test_booking_json_keeps_paid_and_hides_status():
    client = make_client()

    created = client.post(
        "/spaces/1/bookings", json={"member": "annabel", **slot(1, 2)}
    ).get_json()

    assert set(created) == BOOKING_KEYS
    assert created["paid"] is False
    assert stored_status(client.application, created["id"]) == "pending"

    paid = pay(client, created["id"])

    assert set(paid) == BOOKING_KEYS
    assert paid["paid"] is True
    assert paid["card_last4"] == "4242"
    assert stored_status(client.application, created["id"]) == "completed"


def test_booking_times_with_an_offset_are_returned_in_utc():
    client = make_client()
    client.application.db.execute("SET TIME ZONE 'Asia/Bangkok'")

    created = client.post(
        "/spaces/1/bookings",
        json={
            "member": "annabel",
            "start_time": "2030-01-01T10:00:00+07:00",
            "end_time": "2030-01-01T11:00:00+07:00",
        },
    ).get_json()
    found = client.get(f"/bookings/{created['id']}").get_json()

    for booking in (created, found):
        assert booking["start_time"] == "2030-01-01T03:00:00+00:00"
        assert booking["end_time"] == "2030-01-01T04:00:00+00:00"
