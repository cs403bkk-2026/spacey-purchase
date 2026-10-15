"""/pay and refunds through Payments' API (PUR-027), against a stub of Payments.

The answers are built from Payments' openapi.yaml (cs403bkk-2026/spacey-payments
at 0e00172). It has no example for GET /payments 200, POST /payments 202 or
the refund's 200, so those are built from its Payment schema.
"""

import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import psycopg
import pytest
from purchase_helpers import make_client, pay, slot

import purchase.booking
from purchase.logger import logger

TOKEN = "payments-test-token"
CARD = {"card_number": "4242424242424242", "expiry": "12/30", "cvc": "123"}
PAYMENT = {
    "payment_id": 12,
    "booking_id": 1,
    "amount_cents": 2500,
    "currency": "USD",
    "status": "success",
    "reason": None,
    "card_last4": "4242",
    "created_at": "2026-10-09T06:31:24.910522+00:00",
}
FAILED = {**PAYMENT, "payment_id": 13, "status": "failed", "reason": "payment failed"}
UNKNOWN = {**PAYMENT, "payment_id": 14, "status": "unknown"}
REFUND = {**PAYMENT, "payment_id": 57, "status": "refunded", "reason": "cancellation"}
NEVER = object()  # an answer that never comes


class Payments:
    """Records each request. GET /payments lists `ledger`; POST /payments
    charges into it, and a refund of payment 12 refunds it once."""

    def __init__(self):
        self.requests = []
        self.answers = {}  # (method, path) -> (status, body), or NEVER
        self.ledger = []
        self.before_charge = None  # run before POST /payments answers
        self.release = threading.Event()
        self.url = ""

    def answer(self, method, path, status, body=None):
        self.answers[(method, path)] = (status, body)

    def sent(self, method, path):
        return [r for r in self.requests if (r["method"], r["path"]) == (method, path)]

    def respond(self, method, path, body):
        if (method, path) in self.answers:
            return self.answers[(method, path)]
        if (method, path) == ("GET", "/payments"):
            return 200, {"payments": list(self.ledger)}
        if (method, path) == ("POST", "/payments"):
            self.ledger.append(PAYMENT)
            return 201, PAYMENT
        if (method, path) == ("POST", "/payments/12/refund"):
            refunds = [p for p in self.ledger if p["status"] == "refunded"]
            if refunds:
                return 200, refunds[0]
            refund = {**REFUND, "reason": body["reason"]}
            self.ledger.append(refund)
            return 201, refund
        return 404, {"error": "payment not found"}


@pytest.fixture
def payments(monkeypatch):
    state = Payments()

    class Handler(BaseHTTPRequestHandler):
        def handle_any(self):
            parts = urlsplit(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            body = json.loads(raw) if raw else None
            state.requests.append(
                {
                    "method": self.command,
                    "path": parts.path,
                    "query": parse_qs(parts.query),
                    "token": self.headers.get("X-Service-Token"),
                    "body": body,
                }
            )
            charging = (self.command, parts.path) == ("POST", "/payments")
            if charging and state.before_charge:
                state.before_charge()
            answer = state.respond(self.command, parts.path, body)
            if answer is NEVER:
                state.release.wait(10)
                return
            status, payload = answer
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = handle_any  # noqa: N815 - the handler's names

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    monkeypatch.setenv("PAYMENTS_URL", state.url)
    monkeypatch.setenv("SERVICE_TOKEN", TOKEN)
    yield state
    state.release.set()
    server.shutdown()
    server.server_close()


@pytest.fixture
def client():
    """Booking 1: pending at 2500 cents, tomorrow."""
    client = make_client()
    response = client.post(
        "/spaces/1/bookings", json={"member": "annabel", **slot(24, 25)}
    )
    assert response.status_code == 201
    assert response.get_json()["amount_cents"] == 2500
    return client


@pytest.fixture
def logs(client, caplog):
    caplog.set_level(logging.DEBUG, logger=logger.name)
    return caplog


def sql(statement, params=()):
    """On a connection of its own, as another request would."""
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute(statement, params)


def stored(client, booking_id=1):
    with client.application.db.cursor() as cur:
        cur.execute(
            "SELECT status, card_last4 FROM bookings WHERE id = %s", (booking_id,)
        )
        return cur.fetchone()


def take_the_slot():
    """Booking 1's hold runs out and another member books its slot."""
    sql("UPDATE bookings SET status = 'expired' WHERE id = 1")
    sql(
        "INSERT INTO bookings (space_id, member, status, start_time, end_time, "
        "amount_cents) SELECT space_id, 'brook', 'pending', start_time, end_time, "
        "amount_cents FROM bookings WHERE id = 1"
    )


def cancel_it():
    sql("UPDATE bookings SET status = 'cancelled' WHERE id = 1")


def assert_no_secret_logged(caplog, *values):
    for value in (CARD["card_number"], CARD["cvc"], TOKEN, *values):
        assert value not in caplog.text


def payment_lines(caplog):
    return [
        r
        for r in caplog.records
        if r.name == logger.name and r.getMessage().startswith("payment ")
    ]


@pytest.mark.parametrize(
    "answer,expected_status,expected_error",
    [
        ((201, PAYMENT), 200, None),
        ((409, PAYMENT), 200, None),
        ((402, FAILED), 402, "payment failed"),
        (
            (400, {"error": "card_number is not a valid card number"}),
            400,
            "card_number is not a valid card number",
        ),
        ((202, UNKNOWN), 503, "payment unavailable"),
        ((500, {"error": "payment unavailable"}), 503, "payment unavailable"),
        ((401, {"error": "unauthorized"}), 503, "payment unavailable"),
        ((409, {"error": "idempotency_key reused"}), 503, "payment unavailable"),
        (NEVER, 503, "payment unavailable"),
    ],
    ids=[
        "success",
        "paid_other_key",
        "declined",
        "bad_card",
        "unknown",
        "error",
        "unauthorized",
        "key_reused",
        "timeout",
    ],
)
def test_pay(payments, client, logs, answer, expected_status, expected_error):
    payments.answers[("POST", "/payments")] = answer

    started = time.monotonic()
    response = client.post("/bookings/1/pay", json=CARD)

    assert time.monotonic() - started < 3
    assert response.status_code == expected_status
    if expected_status == 200:
        assert response.get_json()["paid"] is True
        assert response.get_json()["card_last4"] == "4242"
        assert stored(client) == {"status": "completed", "card_last4": "4242"}
    else:
        assert response.get_json() == {"error": expected_error}
        assert stored(client) == {"status": "pending", "card_last4": None}
    assert_no_secret_logged(logs)


def test_pay_needs_the_token(payments, client):
    client.environ_base.pop("HTTP_AUTHORIZATION")

    response = client.post("/bookings/1/pay", json=CARD)

    assert response.status_code == 403
    assert response.get_json() == {"error": "invalid service token"}
    assert payments.requests == []


@pytest.mark.parametrize(
    "ledger,key",
    [
        ([], "booking-1-attempt-1"),
        ([FAILED], "booking-1-attempt-2"),
        ([FAILED, UNKNOWN], "booking-1-attempt-2"),
    ],
    ids=["none", "failed", "failed_unknown"],
)
def test_pay_key(payments, client, ledger, key):
    payments.ledger = list(ledger)

    response = client.post("/bookings/1/pay", json=CARD)

    assert response.status_code == 200
    [charge] = payments.sent("POST", "/payments")
    assert charge["token"] == TOKEN
    assert charge["body"] == {
        "booking_id": 1,
        "amount_cents": 2500,
        "currency": "USD",
        **CARD,
        "idempotency_key": key,
        "force_failure": False,
    }


def test_pay_finishes_an_earlier_charge(payments, client):
    payments.ledger = [FAILED, {**PAYMENT, "card_last4": "1881"}]

    response = client.post("/bookings/1/pay", json=CARD)

    assert response.status_code == 200
    assert response.get_json()["card_last4"] == "1881"
    assert payments.sent("POST", "/payments") == []


@pytest.mark.parametrize(
    "case,expected_status,expected_body",
    [
        ("expired", 409, {"error": "booking hold has expired"}),
        ("cancelled", 404, {"error": "booking not found"}),
        ("unknown", 404, {"error": "booking not found"}),
        ("already_paid", 200, None),
    ],
    ids=["expired", "cancelled", "unknown", "already_paid"],
)
def test_pay_checks_the_hold_first(
    payments, client, case, expected_status, expected_body
):
    booking_id = 99 if case == "unknown" else 1
    if case == "expired":
        sql("UPDATE bookings SET created_at = now() - interval '16 minutes'")
    elif case == "cancelled":
        cancel_it()
    elif case == "already_paid":
        expected_body = pay(client, 1, "1881")

    response = client.post(f"/bookings/{booking_id}/pay", json=CARD)

    assert (response.status_code, response.get_json()) == (
        expected_status,
        expected_body,
    )
    assert payments.requests == []


@pytest.mark.parametrize(
    "before_charge,expected_status",
    [(take_the_slot, 409), (cancel_it, 404)],
    ids=["slot_taken", "cancelled"],
)
def test_pay_refunds_what_cannot_be_confirmed(
    payments, client, before_charge, expected_status
):
    payments.before_charge = before_charge

    response = client.post("/bookings/1/pay", json=CARD)

    assert response.status_code == expected_status
    assert stored(client)["status"] != "completed"
    [refund] = payments.sent("POST", "/payments/12/refund")
    assert refund["body"] == {"reason": "hold_expired"}


@pytest.mark.parametrize("case", ["free", "no_url", "no_token"])
def test_pay_without_payments(payments, client, monkeypatch, case):
    if case == "free":
        sql("UPDATE bookings SET amount_cents = 0 WHERE id = 1")
    else:
        monkeypatch.delenv("PAYMENTS_URL" if case == "no_url" else "SERVICE_TOKEN")

    response = client.post("/bookings/1/pay", json=CARD)

    if case == "free":
        assert response.status_code == 200
        assert response.get_json()["paid"] is True
        assert response.get_json()["card_last4"] is None
    else:
        assert response.status_code == 503
        assert response.get_json() == {"error": "payment unavailable"}
    assert payments.requests == []


@pytest.mark.parametrize(
    "case,refund,refunds",
    [
        ("paid", "refunded", 1),
        ("charged_unpaid", "refunded", 1),
        ("no_charge", "no_charge", 0),
        ("refunded_before", "refunded", 1),
        ("down", "unavailable", 0),
        ("started", None, 0),
    ],
    ids=["paid", "charged_unpaid", "no_charge", "refunded_before", "down", "started"],
)
def test_cancel_refunds(payments, client, logs, case, refund, refunds):
    if case == "paid":
        pay(client, 1)
    if case != "no_charge":
        payments.ledger = [PAYMENT]
    if case == "refunded_before":
        payments.ledger.append(REFUND)
    if case == "down":
        payments.answer("GET", "/payments", 500, {"error": "payment unavailable"})
    if case == "started":
        sql("UPDATE bookings SET start_time = now() - interval '1 hour' WHERE id = 1")
    before = client.get("/bookings/1").get_json()

    response = client.delete("/bookings/1")

    if case == "started":
        assert response.status_code == 409
        assert response.get_json() == {"error": "booking has already started"}
        assert payments.requests == []
        return
    assert response.status_code == 200
    assert response.get_json() == {**before, "refund": refund}
    sent = payments.sent("POST", "/payments/12/refund")
    assert [r["body"] for r in sent] == [{"reason": "cancellation"}] * refunds
    if case == "down":
        assert (logs.records[-1].levelno, logs.records[-1].getMessage()) == (
            logging.ERROR,
            "refund booking_id=1 reason=cancellation outcome=unavailable",
        )
    requests = len(payments.requests)
    assert client.delete("/bookings/1").status_code == 404
    assert len(payments.requests) == requests


@pytest.mark.parametrize(
    "case,expected_status,refunds",
    [("slot_taken", 409, 1), ("cancelled", 404, 1), ("confirmed", 200, 0)],
    ids=["slot_taken", "cancelled", "confirmed"],
)
def test_paid_refunds_a_refused_result(
    payments, client, case, expected_status, refunds
):
    payments.ledger = [PAYMENT]
    if case == "slot_taken":
        take_the_slot()
    elif case == "cancelled":
        cancel_it()

    response = client.post("/bookings/1/paid", json={"card_last4": "4242"})

    assert response.status_code == expected_status
    sent = payments.sent("POST", "/payments/12/refund")
    assert [r["body"] for r in sent] == [{"reason": "hold_expired"}] * refunds


def test_without_payments_nothing_changes(payments, client, monkeypatch):
    monkeypatch.delenv("PAYMENTS_URL")
    before = client.get("/bookings/1").get_json()

    paid = client.post("/bookings/1/paid", json={"card_last4": "4242"})
    cancelled = client.delete("/bookings/1")

    assert paid.status_code == 200
    assert cancelled.status_code == 200
    assert cancelled.get_json() == {**before, "paid": True, "card_last4": "4242"}
    assert "refund" not in cancelled.get_json()
    assert payments.requests == []


# The twins of Payments' tests of the hub's mark_booking_paid
# (spacey: payment/tests/test_payment_logging.py, test_payment_race.py).

TWIN_CARD = {"card_number": "4111111111111111", "expiry": "12/99", "cvc": "987"}


@pytest.mark.parametrize(
    "outcome,status",
    [
        ("not_found", 404),
        ("already_paid", 200),
        ("invalid_card", 400),
        ("failed", 402),
        ("succeeded", 200),
    ],
)
def test_outcomes_exclude_card_data(payments, client, logs, outcome, status):
    body = {**TWIN_CARD, "force_failure": outcome == "failed"}
    if outcome == "not_found":
        cancel_it()
    elif outcome == "already_paid":
        pay(client, 1, "1111")
    elif outcome == "invalid_card":
        body["card_number"] = "sensitive-invalid-card"
        payments.answer("POST", "/payments", 400, {"error": "card_number is bad"})
    elif outcome == "failed":
        payments.answer("POST", "/payments", 402, {**FAILED, "card_last4": "1111"})
    else:
        payments.answer("POST", "/payments", 201, {**PAYMENT, "card_last4": "1111"})
    logs.clear()

    response = client.post("/bookings/1/pay", json=body)

    assert response.status_code == status
    lines = [(r.levelno, r.getMessage()) for r in payment_lines(logs)]
    assert lines == [
        (logging.DEBUG, "payment booking_id=1 outcome=started"),
        (
            logging.INFO if status == 200 else logging.WARNING,
            f"payment booking_id=1 outcome={outcome}",
        ),
    ]
    for value in (*TWIN_CARD.values(), "1111", "sensitive-invalid-card", TOKEN):
        assert value not in logs.text


@pytest.mark.parametrize("operation", ["select", "update"])
def test_database_errors_do_not_log_diagnostics(
    payments, client, logs, monkeypatch, operation
):
    error = psycopg.OperationalError(f"SQL parameters: {TWIN_CARD!r}")

    def fail(*args):
        raise error

    name = "get_booking" if operation == "select" else "mark_paid"
    monkeypatch.setattr(purchase.booking, name, fail)

    response = client.post("/bookings/1/pay", json=TWIN_CARD)

    assert (response.status_code, response.get_json()) == (
        500,
        {"error": "payment unavailable"},
    )
    last = [r for r in logs.records if r.name == logger.name][-1]
    assert last.levelno == logging.ERROR
    assert last.getMessage() == "payment booking_id=1 outcome=database_error"
    assert last.exc_info is None
    for value in TWIN_CARD.values():
        assert value not in logs.text


@pytest.mark.parametrize("repeat", ["sequential-repeat"])
def test_same_booking_reaches_mark_paid_once(payments, client, repeat):
    first = client.post("/bookings/1/pay", json=CARD)
    second = client.post("/bookings/1/pay", json=CARD)

    assert first.status_code == second.status_code == 200
    assert first.get_json() == second.get_json()
    assert len(payments.sent("POST", "/payments")) == 1
