"""Run-once schema migrations for the Purchase database.

Rules for the SQL files in this folder:
- A file is named `NNN_<what>.sql` and the files run in name order. A new
  file takes the next free number.
- A merged file is never edited or renumbered: databases that already ran it
  will not run it again.
- A file has no `BEGIN` or `COMMIT`: each file runs in its own transaction,
  together with the row that records it.
"""

import os
from pathlib import Path

from purchase.space import seed_starter_space

MIGRATIONS_DIR = Path(__file__).parent
LOCK_KEY = "hashtext('purchase_schema_migrations')"


def run_migrations(conn) -> None:  # conn: autocommit, dict rows
    """Apply every SQL file not yet recorded in purchase_schema_migrations,
    then seed the starter space when SEED_STARTER_SPACE is "true".

    One advisory lock covers the whole run, so instances that start together
    apply each file once. A failing file raises; it and every later file stay
    unapplied and unrecorded."""
    conn.execute(f"SELECT pg_advisory_lock({LOCK_KEY})")
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS purchase_schema_migrations ("
            "name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        applied = {
            row["name"]
            for row in conn.execute("SELECT name FROM purchase_schema_migrations")
        }
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in applied:
                continue
            with conn.transaction():
                conn.execute(path.read_text())  # no parameters: several statements run
                conn.execute(
                    "INSERT INTO purchase_schema_migrations (name) VALUES (%s)",
                    (path.name,),
                )
        if os.getenv("SEED_STARTER_SPACE", "false").lower() == "true":
            with conn.cursor() as cur:
                seed_starter_space(cur)
    finally:
        conn.execute(f"SELECT pg_advisory_unlock({LOCK_KEY})")
