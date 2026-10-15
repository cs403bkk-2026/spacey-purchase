"""The service's only link to Payments (spacey-payments, ADR 0004).

call(), enabled() and field() are the hub's purchase/client.py ones, with
Payments' X-Service-Token header. A call that gets no answer we can act on
(a timeout, a connection error, 401, 5xx, an unknown outcome, a reused key or
unexpected JSON) raises PaymentsUnavailable, and the booking stays as it was.
Card details only pass through: nothing here logs them, a token or a body.
"""

import json
import os
from http.client import HTTPException  # http.client's, not Werkzeug's
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from purchase.logger import logger

TIMEOUT_S = 2  # /pay makes at most 4 calls; the hub waits 10 s


class PaymentsUnavailable(Exception):  # noqa: N818 - the hub's name
    """Payments gave no answer we can act on."""


def enabled() -> bool:
    """Both settings are needed; a missing one never stops the start."""
    return bool(os.getenv("PAYMENTS_URL")) and bool(os.getenv("SERVICE_TOKEN"))


def call(method, path, body=None, expect=(200,)):
    url = os.environ["PAYMENTS_URL"].rstrip("/") + path
    data = None if body is None else json.dumps(body).encode()
    try:  # Request() raises ValueError on a bad URL
        request = Request(
            url,
            data,
            method=method,
            headers={
                "X-Service-Token": os.getenv("SERVICE_TOKEN", ""),
                "Content-Type": "application/json",
            },
        )
        try:
            # One try, no retry: the member's next Pay is the retry.
            with urlopen(request, timeout=TIMEOUT_S) as response:
                status, raw = response.status, response.read()
        except HTTPError as error:  # 4xx/5xx carry the answer
            status, raw = error.code, error.read()
        if status not in expect:  # 401 included: never passed to the frontend
            raise PaymentsUnavailable(f"{method} {path}: {status}")
        return json.loads(raw), status
    except (OSError, ValueError, HTTPException) as error:
        raise PaymentsUnavailable(f"{method} {path}: {type(error).__name__}") from None


def field(payload, key):
    """payload[key]. A JSON answer without it is a link failure too."""
    try:
        return payload[key]
    except (KeyError, TypeError):
        raise PaymentsUnavailable(f"answer without {key!r}") from None


def listed(booking_id) -> list:
    """The booking's payments and refunds, oldest first."""
    payload, _ = call("GET", f"/payments?booking_id={booking_id}")
    payments = field(payload, "payments")
    if not isinstance(payments, list):
        raise PaymentsUnavailable("'payments' is not a list")
    return payments


def success(booking_id) -> dict | None:
    """The booking's success payment; Payments keeps at most one."""
    for payment in listed(booking_id):
        if field(payment, "status") == "success":
            return payment
    return None


def charge(booking_id, amount_cents, card_number, expiry, cvc, force_failure):
    """(success payment, 201), or Payments' 400 or 402 answer."""
    payments = listed(booking_id)
    for payment in payments:
        if field(payment, "status") == "success":
            return payment, 201  # charged earlier, not marked paid yet
    # ADR 0004 rule 2: a new key only after a confirmed failure.
    failed = sum(field(p, "status") == "failed" for p in payments)
    payment, status = call(
        "POST",
        "/payments",
        {
            "booking_id": booking_id,
            "amount_cents": amount_cents,
            "currency": "USD",
            "card_number": card_number,
            "expiry": expiry,
            "cvc": cvc,
            "idempotency_key": f"booking-{booking_id}-attempt-{failed + 1}",
            "force_failure": force_failure,
        },
        expect=(201, 202, 400, 402, 409),
    )
    if status == 400:
        return {"error": field(payment, "error")}, 400  # the card's first problem
    if status == 402:
        return payment, 402
    if field(payment, "status") != "success":  # 202 unknown; 409 key reused
        raise PaymentsUnavailable(f"POST /payments: {status}")
    field(payment, "card_last4")  # a success without it is unexpected JSON
    return payment, 201  # a 409 with a success: paid under another key


def refund(booking_id, reason) -> str:
    """Refund the booking's success in full: "refunded", or "no_charge"."""
    payment = success(booking_id)
    if payment is None:
        return "no_charge"
    call(
        "POST",
        f"/payments/{field(payment, 'payment_id')}/refund",
        {"reason": reason},
        expect=(200, 201),  # 200: refunded before
    )
    return "refunded"


def check_link() -> None:
    """Log whether Payments answers this service with its token, so the
    turn-on can be checked in our log (PUR-027, gunicorn.conf.py)."""
    if not enabled():
        return
    try:
        call("GET", "/payments?booking_id=1")
        logger.info("payments check: ok")
    except PaymentsUnavailable as error:
        logger.warning("payments check: failed (%s)", error)
    except Exception as error:  # a log line must never stop the start
        logger.warning("payments check: failed (%s)", type(error).__name__)
