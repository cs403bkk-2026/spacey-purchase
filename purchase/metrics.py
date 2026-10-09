def compute_metrics(cur) -> dict:
    """Compute Purchase metrics. See ADR 0001 for the service boundary."""
    cur.execute("SELECT COUNT(*) AS count FROM spaces")
    total_spaces = cur.fetchone()["count"]

    cur.execute(
        "SELECT COUNT(*) AS total, "
        "COUNT(*) FILTER (WHERE paid) AS paid, "
        "COUNT(*) FILTER (WHERE NOT paid) AS unpaid "
        "FROM bookings"
    )
    booking_counts = cur.fetchone()

    cur.execute("SELECT COUNT(DISTINCT member) AS count FROM bookings")
    total_members = cur.fetchone()["count"]

    cur.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) AS total FROM bookings WHERE paid"
    )
    revenue_cents = cur.fetchone()["total"]

    # Booked hours over the next 7 days, clipped to that window, across all
    # spaces - same overlap rule used everywhere else (starts before the
    # window ends AND ends after the window starts).
    cur.execute(
        "SELECT COALESCE(SUM(EXTRACT(EPOCH FROM ("
        "LEAST(end_time, now() + interval '7 days') - GREATEST(start_time, now())"
        ")) / 3600.0), 0) AS hours "
        "FROM bookings "
        "WHERE start_time < now() + interval '7 days' AND end_time > now()"
    )
    booked_hours = float(cur.fetchone()["hours"])
    available_hours = total_spaces * 7 * 24
    utilization = booked_hours / available_hours if available_hours > 0 else 0.0

    cur.execute(
        "SELECT COUNT(*) AS count FROM ("
        "SELECT member FROM bookings GROUP BY member HAVING COUNT(*) > 1"
        ") repeat_members"
    )
    repeat_members = cur.fetchone()["count"]
    repeat_member_rate = repeat_members / total_members if total_members > 0 else 0.0

    payment_conversion = (
        booking_counts["paid"] / booking_counts["total"]
        if booking_counts["total"] > 0
        else 0.0
    )

    avg_revenue_cents_per_paid_booking = (
        round(revenue_cents / booking_counts["paid"])
        if booking_counts["paid"] > 0
        else 0
    )

    cur.execute(
        "SELECT s.id, s.name, "
        "COALESCE(SUM(b.amount_cents) FILTER (WHERE b.paid), 0) AS revenue_cents "
        "FROM spaces s LEFT JOIN bookings b ON b.space_id = s.id "
        "GROUP BY s.id, s.name ORDER BY s.id"
    )
    revenue_by_space = cur.fetchall()

    return {
        "spaces": total_spaces,
        "bookings": booking_counts["total"],
        "paid_bookings": booking_counts["paid"],
        "unpaid_bookings": booking_counts["unpaid"],
        "members": total_members,
        "revenue_cents": revenue_cents,
        "utilization": utilization,
        "repeat_member_rate": repeat_member_rate,
        "payment_conversion": payment_conversion,
        "avg_revenue_cents_per_paid_booking": avg_revenue_cents_per_paid_booking,
        "revenue_by_space": revenue_by_space,
    }
