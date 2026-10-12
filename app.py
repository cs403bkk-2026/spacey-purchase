import hmac
import os

import psycopg
from flask import Flask, jsonify, request
from psycopg.rows import dict_row

from purchase.api import purchase_bp
from purchase.migrations import run_migrations
from purchase.space import seed_starter_space


def required_setting(name: str) -> str:
    value = os.getenv(name, "")
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value


def connect(database_url: str) -> psycopg.Connection:
    try:
        return psycopg.connect(database_url, row_factory=dict_row, autocommit=True)
    except psycopg.Error:
        # No libpq text and no chained error: both can echo the URL's password.
        raise RuntimeError("cannot connect to the database in DATABASE_URL") from None


def create_app() -> Flask:
    database_url = required_setting("DATABASE_URL")
    token = required_setting("PURCHASE_TOKEN")

    app = Flask(__name__)
    app.db = connect(database_url)
    run_migrations(app.db)
    app.register_blueprint(purchase_bp)

    @app.before_request
    def require_token():
        if request.endpoint == "health":
            return None
        sent = request.headers.get("Authorization", "").encode()
        if not hmac.compare_digest(sent, f"Bearer {token}".encode()):
            return jsonify(error="invalid service token"), 403
        return None

    @app.get("/health")
    def health():
        try:
            with app.db.cursor() as cur:
                cur.execute("SELECT 1")
        except psycopg.Error:
            return jsonify(status="error", error="database unreachable"), 503
        return jsonify(status="ok", revision=os.getenv("APP_REVISION", "local"))

    if os.getenv("PURCHASE_TEST_RESET") == "1":

        @app.post("/test/reset")
        def reset():
            with app.db.cursor() as cur:
                cur.execute(
                    "TRUNCATE bookings, spaces, subscriptions, users RESTART IDENTITY"
                )
                if os.getenv("SEED_STARTER_SPACE", "false").lower() == "true":
                    seed_starter_space(cur)
            return "", 204

    return app
