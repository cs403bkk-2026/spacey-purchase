"""HTTP routes of the Purchase service. Each route parses the request, calls
one domain function and shapes the response - no business rules or SQL here."""

import re
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request

import purchase.booking
import purchase.member
import purchase.metrics
import purchase.space

purchase_bp = Blueprint("purchase", __name__)


def parse_time(value) -> datetime | None:
    """ISO 8601 with a timezone, e.g. 2026-09-25T09:00:00+07:00."""
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def parse_window(args) -> tuple[tuple | None, str | None]:
    """Optional ?start_time=&end_time= as (window, error); window is None if absent."""
    raw_start, raw_end = args.get("start_time"), args.get("end_time")
    if raw_start is None and raw_end is None:
        return None, None
    start_time, end_time = parse_time(raw_start), parse_time(raw_end)
    if start_time is None or end_time is None:
        return None, (
            "start_time and end_time must be given together "
            "(ISO 8601 with timezone, e.g. 2026-09-25T09:00:00Z)"
        )
    if end_time <= start_time:
        return None, "end_time must be after start_time"
    return (start_time, end_time), None


def json_body():
    """The request's JSON object; any other body (none, 42, a list) counts as {}."""
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


@purchase_bp.post("/register")
def register():
    body = json_body()
    with current_app.db.cursor() as cur:
        payload, status = purchase.member.register_user(
            cur, body.get("email"), body.get("password")
        )
    return jsonify(payload), status


@purchase_bp.post("/users/authenticate")
def authenticate():
    """Checks a login for the hub, which keeps the session; sets no cookie."""
    body = json_body()
    with current_app.db.cursor() as cur:
        payload, status = purchase.member.authenticate(
            cur, body.get("email"), body.get("password")
        )
    return jsonify(payload), status


@purchase_bp.get("/users/<int:user_id>/bookings")
def list_user_bookings(user_id):
    with current_app.db.cursor() as cur:
        rows = purchase.booking.list_user_bookings(cur, user_id)
    return jsonify(bookings=[purchase.booking.booking_to_json(row) for row in rows])


@purchase_bp.get("/spaces")
def list_spaces():
    window, error = parse_window(request.args)
    if error:
        return jsonify(error=error), 400
    with current_app.db.cursor() as cur:
        rows = purchase.space.list_spaces(cur)
        booked_ids = purchase.space.booked_space_ids(cur, window)
    spaces = [{**row, "available": row["id"] not in booked_ids} for row in rows]
    return jsonify(spaces=spaces)


@purchase_bp.get("/spaces/booked-ids")
def booked_space_ids():
    window, error = parse_window(request.args)
    if error:
        return jsonify(error=error), 400
    with current_app.db.cursor() as cur:
        booked_ids = purchase.space.booked_space_ids(cur, window)
    return jsonify(space_ids=sorted(booked_ids))


@purchase_bp.post("/spaces")
def create_space():
    body = json_body()
    with current_app.db.cursor() as cur:
        payload, status = purchase.space.create_space(
            cur, body.get("name"), body.get("capacity"), body.get("price_cents", 0)
        )
    return jsonify(payload), status


@purchase_bp.get("/spaces/<int:space_id>")
def get_space(space_id):
    window, error = parse_window(request.args)
    if error:
        return jsonify(error=error), 400
    with current_app.db.cursor() as cur:
        space = purchase.space.get_space(cur, space_id)
        if space is None:
            return jsonify(error="space not found"), 404
        booked = space_id in purchase.space.booked_space_ids(cur, window)
    return jsonify({**space, "available": not booked})


@purchase_bp.patch("/spaces/<int:space_id>")
def update_space(space_id):
    body = json_body()
    with current_app.db.cursor() as cur:
        payload, status = purchase.space.update_space(cur, space_id, body)
    return jsonify(payload), status


@purchase_bp.delete("/spaces/<int:space_id>")
def delete_space(space_id):
    with current_app.db.cursor() as cur:
        payload, status = purchase.space.delete_space(cur, space_id)
    if payload is None:
        return "", status
    return jsonify(payload), status


@purchase_bp.get("/spaces/<int:space_id>/bookings")
def list_space_bookings(space_id):
    with current_app.db.cursor() as cur:
        if purchase.space.get_space(cur, space_id) is None:
            return jsonify(error="space not found"), 404
        rows = purchase.booking.list_space_bookings(cur, space_id)
    return jsonify(bookings=[purchase.booking.booking_to_json(row) for row in rows])


@purchase_bp.post("/spaces/<int:space_id>/bookings")
def create_booking(space_id):
    body = json_body()
    start_time = parse_time(body.get("start_time"))
    end_time = parse_time(body.get("end_time"))
    if start_time is None or end_time is None:
        return jsonify(
            error="start_time and end_time are required (ISO 8601 with timezone)"
        ), 400
    with current_app.db.cursor() as cur:
        payload, status = purchase.booking.create_booking(
            cur,
            space_id,
            body.get("member"),
            start_time,
            end_time,
            body.get("party_size", 1),
            body.get("user_id"),
        )
    if status == 201:
        payload = purchase.booking.booking_to_json(payload)
    return jsonify(payload), status


@purchase_bp.get("/bookings")
def list_bookings():
    with current_app.db.cursor() as cur:
        rows = purchase.booking.list_bookings(cur)
    return jsonify(bookings=[purchase.booking.booking_to_json(row) for row in rows])


@purchase_bp.get("/bookings/<int:booking_id>")
def find_booking(booking_id):
    with current_app.db.cursor() as cur:
        row = purchase.booking.get_booking(cur, booking_id)
    if row is None:
        return jsonify(error="booking not found"), 404
    return jsonify(purchase.booking.booking_to_json(row))


@purchase_bp.delete("/bookings/<int:booking_id>")
def cancel_booking(booking_id):
    with current_app.db.cursor() as cur:
        row = purchase.booking.cancel_booking(cur, booking_id)
    if row is None:
        return jsonify(error="booking not found"), 404
    return jsonify(purchase.booking.booking_to_json(row))


@purchase_bp.get("/metrics")
def metrics():
    with current_app.db.cursor() as cur:
        data = purchase.metrics.compute_metrics(cur)
    return jsonify(data)


@purchase_bp.get("/bookings/<int:booking_id>/payable")
def check_payable(booking_id):
    with current_app.db.cursor() as cur:
        payload, status = purchase.booking.check_payable(cur, booking_id)
    return jsonify(payload), status


@purchase_bp.post("/bookings/<int:booking_id>/paid")
def booking_paid(booking_id):
    body = request.get_json(silent=True)
    # Only the last 4 digits may ever reach Purchase.
    if not (
        isinstance(body, dict)
        and body.keys() == {"card_last4"}
        and isinstance(body["card_last4"], str)
        and re.fullmatch("[0-9]{4}", body["card_last4"])
    ):
        return jsonify(error="send only card_last4, as 4 digits"), 400
    with current_app.db.cursor() as cur:
        row = purchase.booking.mark_paid(cur, booking_id, body["card_last4"])
    if row is None:
        return jsonify(error="booking not found"), 404
    return jsonify(purchase.booking.booking_to_json(row)), 200
