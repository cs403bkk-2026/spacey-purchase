import os
import traceback

import pytest

from app import create_app
from purchase.migrations import MIGRATIONS_DIR


def make_client():
    return create_app().test_client()


def auth(token=None):
    return {"Authorization": f"Bearer {token or os.environ['PURCHASE_TOKEN']}"}


def add_spaces(app, count):
    for number in range(count):
        app.db.execute(
            "INSERT INTO spaces (name, capacity) VALUES (%s, 1)", (f"Room {number}",)
        )


def space_count(app):
    return app.db.execute("SELECT COUNT(*) AS count FROM spaces").fetchone()["count"]


def test_health_reports_local_revision_when_unset(monkeypatch):
    monkeypatch.delenv("APP_REVISION", raising=False)
    client = make_client()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "revision": "local"}


def test_health_reports_running_revision(monkeypatch):
    monkeypatch.setenv("APP_REVISION", "test-revision")
    client = make_client()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "revision": "test-revision"}


def test_health_reports_error_when_database_is_unreachable():
    client = make_client()
    client.application.db.close()

    response = client.get("/health")

    assert response.status_code == 503
    assert response.get_json() == {"status": "error", "error": "database unreachable"}


@pytest.mark.parametrize("value", [None, ""], ids=["unset", "empty"])
@pytest.mark.parametrize("name", ["DATABASE_URL", "PURCHASE_TOKEN"])
def test_start_fails_without_a_setting(monkeypatch, name, value):
    if value is None:
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError) as error:
        create_app()

    assert str(error.value) == f"{name} is not set"


def test_start_fails_with_an_unreachable_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:s3cret@localhost:1/x")

    with pytest.raises(RuntimeError) as error:
        create_app()

    assert "s3cret" not in "".join(traceback.format_exception(error.value))


def test_start_twice_applies_each_migration_once():
    create_app()
    app = create_app()

    names = [
        row["name"]
        for row in app.db.execute("SELECT name FROM purchase_schema_migrations")
    ]
    assert sorted(names) == sorted(path.name for path in MIGRATIONS_DIR.glob("*.sql"))


@pytest.mark.parametrize(
    "header",
    [None, "Bearer wrong", "Bearer ", "test-token", "Basic dGVzdC10b2tlbg=="],
    ids=["none", "wrong", "empty", "bare", "basic"],
)
def test_missing_or_wrong_token_is_refused(header):
    client = make_client()
    add_spaces(client.application, 1)
    before = space_count(client.application)
    headers = {} if header is None else {"Authorization": header}

    reset = client.post("/test/reset", headers=headers)
    nowhere = client.get("/nowhere", headers=headers)

    for response in (reset, nowhere):
        assert response.status_code == 403
        assert response.get_json() == {"error": "invalid service token"}
    assert space_count(client.application) == before
    assert client.get("/nowhere", headers=auth()).status_code == 404


@pytest.mark.parametrize("value", [None, "0", "true"], ids=["unset", "0", "true"])
def test_reset_exists_only_with_the_flag(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("PURCHASE_TEST_RESET")
    else:
        monkeypatch.setenv("PURCHASE_TEST_RESET", value)
    client = make_client()
    add_spaces(client.application, 1)
    before = space_count(client.application)

    response = client.post("/test/reset", headers=auth())

    assert response.status_code == 404
    assert space_count(client.application) == before


@pytest.mark.parametrize(
    ("seed", "expected"),
    [("true", [{"id": 1, "name": "Founders Desk"}]), ("false", [])],
    ids=["on", "off"],
)
def test_reset_restarts_ids_and_seeds_only_with_the_flag(monkeypatch, seed, expected):
    monkeypatch.setenv("SEED_STARTER_SPACE", seed)
    client = make_client()
    add_spaces(client.application, 2)

    response = client.post("/test/reset", headers=auth())

    assert response.status_code == 204
    spaces = client.application.db.execute(
        "SELECT id, name FROM spaces ORDER BY id"
    ).fetchall()
    assert spaces == expected
