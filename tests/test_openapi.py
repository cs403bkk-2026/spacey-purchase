from pathlib import Path

import yaml

METHODS = {"get", "post", "put", "patch", "delete"}
SPEC = yaml.safe_load((Path(__file__).parent.parent / "openapi.yaml").read_text())
CONTRACT = {
    "GET /health",
    "GET /spaces",
    "POST /spaces",
    "GET /spaces/{spaceId}",
    "PATCH /spaces/{spaceId}",
    "DELETE /spaces/{spaceId}",
    "GET /spaces/{spaceId}/bookings",
    "GET /bookings",
    "GET /bookings/{bookingId}",
    "POST /register",
    "GET /metrics",
    "POST /spaces/{spaceId}/bookings",
    "GET /spaces/booked-ids",
    "DELETE /bookings/{bookingId}",
    "GET /users/{userId}/bookings",
    "POST /users/authenticate",
    "POST /bookings/{bookingId}/paid",
    "GET /bookings/{bookingId}/payable",
    "GET /bookings/{bookingId}/unlockable",
    "GET /bookings/{bookingId}/cancellable",
    "POST /test/reset",
}
SPACE = {"id", "name", "capacity", "price_cents"}
BOOKING = {"id", "space_id", "member", "paid", "start_time", "end_time"}
BOOKING |= {"amount_cents", "user_id", "card_last4", "created_at"}
WRITES = {  # the success answer the hub's mirror copies into the old tables
    ("POST /spaces", "201"): SPACE,
    ("PATCH /spaces/{spaceId}", "200"): SPACE,
    ("POST /spaces/{spaceId}/bookings", "201"): BOOKING,
    ("POST /bookings/{bookingId}/paid", "200"): BOOKING,
    ("POST /register", "201"): {"id", "email"},
}


def resolve(node):
    while "$ref" in node:
        *_, kind, name = node["$ref"].split("/")
        node = SPEC["components"][kind][name]
    return node


def operations():
    for path, item in SPEC["paths"].items():
        for method in METHODS & item.keys():
            yield f"{method.upper()} {path}", item[method]


def test_operations_are_exactly_the_contract():
    assert {name for name, _ in operations()} == CONTRACT


def test_every_operation_but_health_needs_the_token_and_documents_403():
    assert SPEC["security"] == [{"bearerAuth": []}]
    for name, op in operations():
        open_ = name == "GET /health"
        assert (op.get("security") == []) is open_, name
        assert ("403" in op["responses"]) is not open_, name


def test_only_authenticate_answers_401():
    assert [n for n, op in operations() if "401" in op["responses"]] == [
        "POST /users/authenticate"
    ]


def test_every_response_body_has_an_example_and_errors_are_error_objects():
    for name, op in operations():
        for status, response in op["responses"].items():
            for media in resolve(response).get("content", {}).values():
                assert "example" in media, f"{name} {status}"
                if status.startswith(("4", "5")) and name != "GET /health":
                    assert set(media["example"]) == {"error"}, f"{name} {status}"


def test_every_write_answers_the_full_row():
    ops = dict(operations())
    for (name, status), keys in WRITES.items():
        media = resolve(ops[name]["responses"][status])["content"]["application/json"]
        assert keys <= set(resolve(media["schema"])["required"]), name
        assert keys <= set(media["example"]), name
