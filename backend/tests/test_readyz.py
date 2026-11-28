import pytest
from fastapi.testclient import TestClient

from app.main import app


def test_readyz_ok(db_client: TestClient) -> None:
    r = db_client.get("/readyz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_readyz_database_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@127.0.0.1:1/test")
    monkeypatch.setenv("DATABASE_CONNECT_TIMEOUT", "1")
    with TestClient(app) as client:
        r = client.get("/readyz")
    assert r.status_code == 503
    assert r.json() == {"detail": "database unavailable"}
