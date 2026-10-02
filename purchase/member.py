"""Member helpers: subscriptions, which cover a member's bookings."""

from datetime import timezone


def member_key(name: str) -> str:
    return name.strip().lower()


def is_subscribed(cur, member) -> bool:
    if not isinstance(member, str):
        return False
    cur.execute(
        "SELECT 1 FROM subscriptions WHERE member = %s AND active",
        (member_key(member),),
    )
    return cur.fetchone() is not None


def subscribe(cur, name):
    """Subscribe a member by name. Mocked, like payment: no provider, always
    succeeds. Subscribing again is a no-op, so a retried request can't break
    anything. Returns (payload, status)."""
    member = member_key(name)
    if not member:
        return {"error": "member name must not be blank"}, 400

    cur.execute(
        "INSERT INTO subscriptions (member) VALUES (%s) "
        "ON CONFLICT (member) DO UPDATE SET active = TRUE "
        "RETURNING member, active, started_at",
        (member,),
    )
    row = cur.fetchone()
    return {
        "member": row["member"],
        "active": row["active"],
        "started_at": row["started_at"].astimezone(timezone.utc).isoformat(),
    }, 200
