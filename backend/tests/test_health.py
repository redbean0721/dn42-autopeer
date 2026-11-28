import logging

from fastapi.testclient import TestClient

from app.main import _ProbeAccessFilter, app

client = TestClient(app)


def test_healthz() -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def _access_record(path: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("10.0.0.1:1234", "GET", path, "1.1", 200),
        exc_info=None,
    )


def test_probe_access_log_is_filtered() -> None:
    f = _ProbeAccessFilter()
    assert f.filter(_access_record("/healthz")) is False
    assert f.filter(_access_record("/healthz?probe=1")) is False
    assert f.filter(_access_record("/readyz")) is False
    assert f.filter(_access_record("/api/peers")) is True
