"""HTTP routes for paying a booking. Parses the request, calls purchase.booking
(which asks payment.services whether the card is accepted), and shapes the
response - no business rules or SQL here."""

from flask import Blueprint, current_app, jsonify, redirect, request, url_for

import purchase.booking

booking_bp = Blueprint("booking", __name__)


@booking_bp.post("/bookings/<int:booking_id>/pay")
def pay_booking(booking_id):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        body = {}
    with current_app.db.cursor() as cur:
        payload, status = purchase.booking.mark_booking_paid(
            cur,
            booking_id,
            body.get("card_number"),
            body.get("expiry"),
            body.get("cvc"),
            force_failure=body.get("force_failure") is True,
        )
    if status == 200:
        payload = purchase.booking.booking_to_json(payload)
    return jsonify(payload), status


@booking_bp.post("/bookings/<int:booking_id>/confirmation/pay")
def pay_from_confirmation(booking_id):
    with current_app.db.cursor() as cur:
        payload, status = purchase.booking.mark_booking_paid(
            cur,
            booking_id,
            request.form.get("card_number"),
            request.form.get("expiry"),
            request.form.get("cvc"),
        )
    if status >= 400:
        return redirect(
            url_for("booking_confirmation", booking_id=booking_id, error=payload["error"])
        )
    return redirect(url_for("booking_confirmation", booking_id=booking_id))
