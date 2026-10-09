import os

import psycopg
from flask import Flask, jsonify, redirect
from psycopg.rows import dict_row

from payment.migrations import run_migrations
import purchase.schema
import purchase.space
from purchase.api import purchase_bp

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://spacey:spacey@localhost:5432/spacey"
)
# Signs the login session cookie. Fine for local/dev; a real deployment
# must set a real SECRET_KEY (see issue #47), or every restart logs
# everyone out and, worse, an unset default would be a known, public key.
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-not-for-production")

# Business metrics now live in Grafana (#207). /dashboard redirects there;
# /metrics (JSON) stays part of the API.
REPORTING_URL = os.getenv(
    "REPORTING_URL", "https://grafana.cs403bkk26.space/d/spacey-reporting"
)

def get_connection(database_url: str) -> psycopg.Connection:
    try:
        conn = psycopg.connect(
            database_url, row_factory=dict_row, autocommit=True)
    except psycopg.OperationalError as error:
        # A raw psycopg traceback here is the first thing a new contributor
        # sees if Postgres isn't running yet - fail fast with a clear pointer
        # instead. See the Configuration section in README.md.
        raise SystemExit(
            f"Could not connect to the database at DATABASE_URL={database_url!r}\n"
            f"{error}\n"
            "Is Postgres running? Try: docker compose up db -d"
        ) from None
    with conn.cursor() as cur:
        purchase.schema.create_tables(cur)
    # PT-016: separate payments table (enum status and currency).
    run_migrations(conn)
    return conn

def reset_tables(conn: psycopg.Connection) -> None:
    """Wipe all rows and restart ids. Only used for tests and an opt-in
    local reset (RESET_DB_ON_START=true) - off by default, so a real
    deployment's data survives an app restart."""
    with conn.cursor() as cur:
        cur.execute(
            "TRUNCATE access, bookings, spaces, subscriptions, users, payments "
            "RESTART IDENTITY CASCADE"
        )

def create_app(
    database_url: str = DATABASE_URL, reset_on_start: bool | None = None
) -> Flask:
    if reset_on_start is None:
        reset_on_start = os.getenv(
            "RESET_DB_ON_START", "false").lower() == "true"

    app = Flask(__name__)
    app.secret_key = SECRET_KEY
    app.db = get_connection(database_url)
    app.register_blueprint(purchase_bp)
    if reset_on_start:
        reset_tables(app.db)
    with app.db.cursor() as cur:
        purchase.space.seed_starter_space(cur)

    @app.get("/")
    def index():
        # The browser client lives at /app/ (spacey-frontend). This process
        # is the JSON API.
        return redirect("/app/", code=302)

    @app.get("/health")
    def health():
        try:
            with app.db.cursor() as cur:
                cur.execute("SELECT 1")
        except psycopg.Error:
            return jsonify(status="error", error="database unreachable"), 503

        return jsonify(
            status="ok",
            revision=os.getenv("APP_REVISION", "local"),
        )

    @app.get("/dashboard")
    def dashboard():
        # A temporary redirect, so bookmarks follow the dashboard and rolling back
        # this change is not undone by a browser's cached permanent redirect.
        return redirect(REPORTING_URL, code=302)

    return app

app = create_app()
