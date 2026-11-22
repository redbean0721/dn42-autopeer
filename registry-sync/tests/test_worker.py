import json
import logging
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import psycopg
import pytest
from pydantic import SecretStr, ValidationError

from registry_sync.config import Settings
from registry_sync.server import make_server
from registry_sync.worker import SyncWorker

from .conftest import git


def make_worker(tmp_path: Path, remote: Path, db_url: str, **kw: Any) -> SyncWorker:
    settings = Settings(
        repo_url=f"file://{remote}",
        workdir=tmp_path / "work" / "registry",
        database_url=SecretStr(db_url),
        git_username="u",
        git_token=SecretStr("tok"),
        **{"min_objects": 1, **kw},
    )
    return SyncWorker(settings)


@pytest.mark.parametrize("missing", ["REGISTRY_GIT_USERNAME", "REGISTRY_GIT_TOKEN"])
def test_git_credentials_are_required(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    monkeypatch.setenv("REGISTRY_GIT_USERNAME", "u")
    monkeypatch.setenv("REGISTRY_GIT_TOKEN", "tok")
    monkeypatch.delenv(missing)
    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize("empty", ["REGISTRY_GIT_USERNAME", "REGISTRY_GIT_TOKEN"])
def test_empty_git_credentials_are_rejected(monkeypatch: pytest.MonkeyPatch, empty: str) -> None:
    monkeypatch.setenv("REGISTRY_GIT_USERNAME", "u")
    monkeypatch.setenv("REGISTRY_GIT_TOKEN", "tok")
    monkeypatch.setenv(empty, "")
    with pytest.raises(ValidationError):
        Settings()


def test_run_once_imports_then_follows_new_commits(
    tmp_path: Path, remote_repo: Path, db_url: str
) -> None:
    worker = make_worker(tmp_path, remote_repo, db_url)
    assert not worker.is_ready()
    assert worker.run_once()
    status = worker.status()
    assert status["commit"] == git(remote_repo, "rev-parse", "HEAD")
    assert status["object_count"] == 11
    assert worker.is_ready()

    assert worker.run_once()
    assert worker.status()["commit"] == status["commit"]

    (remote_repo / "data" / "person" / "NEW-DN42").write_text("person: New\nnic-hdl: NEW-DN42\n")
    git(remote_repo, "add", "-A")
    git(remote_repo, "commit", "-q", "-m", "add")
    assert worker.run_once()
    assert worker.status()["object_count"] == 12


def test_refuses_suspiciously_small_checkout(
    tmp_path: Path, remote_repo: Path, db_url: str
) -> None:
    worker = make_worker(tmp_path, remote_repo, db_url, min_objects=1000)
    assert not worker.run_once()
    assert "refusing to import" in (worker.status()["last_error"] or "")
    assert not worker.is_ready()


def test_http_endpoints(tmp_path: Path, remote_repo: Path, db_url: str) -> None:
    worker = make_worker(tmp_path, remote_repo, db_url, trigger_cooldown=60)
    server = make_server(worker, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(path: str, method: str = "GET") -> tuple[int, dict[str, Any]]:
        req = urllib.request.Request(base + path, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.load(resp)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    try:
        assert call("/healthz")[0] == 200
        assert call("/readyz")[0] == 503
        worker.run_once()
        assert call("/readyz")[0] == 200
        code, body = call("/status")
        assert code == 200 and body["object_count"] == 11
        assert call("/sync", "POST") == (202, {"status": "scheduled"})
        code, body = call("/sync", "POST")
        assert code == 429 and body["retry_after"] > 0
        assert call("/nope")[0] == 404
    finally:
        server.shutdown()


def test_recovers_after_schema_is_recreated(
    tmp_path: Path, remote_repo: Path, db_url: str, admin_url: str
) -> None:
    worker = make_worker(tmp_path, remote_repo, db_url)
    assert worker.run_once()

    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute("DROP SCHEMA registry CASCADE")
        admin.execute("CREATE SCHEMA registry AUTHORIZATION registry_sync")

    assert not worker.run_once()
    assert worker.run_once()
    assert worker.status()["object_count"] == 11


def test_logs_up_to_date_once_per_process(
    tmp_path: Path, remote_repo: Path, db_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    make_worker(tmp_path, remote_repo, db_url).run_once()

    caplog.set_level(logging.INFO, logger="registry_sync.worker")
    worker = make_worker(tmp_path, remote_repo, db_url)
    assert worker.run_once()
    assert worker.run_once()
    messages = [r.getMessage() for r in caplog.records if "up to date" in r.getMessage()]
    assert messages == [
        f"registry up to date at {git(remote_repo, 'rev-parse', 'HEAD')[:12]} (11 objects)"
    ]
