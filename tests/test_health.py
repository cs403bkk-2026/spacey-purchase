from app import create_app


def make_client():
    return create_app().test_client()


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
