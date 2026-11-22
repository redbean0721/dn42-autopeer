import logging
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from . import db
from .config import Settings
from .gitrepo import RegistryRepo
from .snapshot import load_registry

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Status:
    commit: str | None = None
    commit_time: datetime | None = None
    synced_at: datetime | None = None
    object_count: int = 0
    last_attempt: datetime | None = None
    last_success: datetime | None = None
    last_error: str | None = None


class SyncWorker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.repo = RegistryRepo(
            path=settings.workdir,
            url=settings.repo_url,
            branch=settings.branch,
            timeout=settings.git_timeout,
            username=settings.git_username,
            token=settings.git_token.get_secret_value(),
        )
        self._conninfo = settings.database_url.get_secret_value()
        self._lock = threading.Lock()
        self._status = Status()
        self._trigger = threading.Event()
        self._stop = threading.Event()
        self._last_trigger = 0.0
        self._heartbeat = time.monotonic()
        self._imports = 0
        self._schema_ready = False
        self._reported = False

    def status(self) -> dict[str, Any]:
        with self._lock:
            data = asdict(self._status)
        return {k: v.isoformat() if isinstance(v, datetime) else v for k, v in data.items()}

    def is_ready(self) -> bool:
        with self._lock:
            return self._status.commit is not None

    def is_alive(self) -> bool:
        s = self.settings
        budget = max(s.sync_interval, s.retry_interval) + 3 * s.git_timeout + 300
        return time.monotonic() - self._heartbeat < budget

    def request_sync(self) -> float:
        with self._lock:
            wait = self._last_trigger + self.settings.trigger_cooldown - time.monotonic()
            if wait > 0:
                return wait
            self._last_trigger = time.monotonic()
        self._trigger.set()
        return 0.0

    def stop(self) -> None:
        self._stop.set()
        self._trigger.set()

    def run_once(self) -> bool:
        with self._lock:
            self._status.last_attempt = datetime.now().astimezone()
        try:
            self._sync()
        except Exception as exc:
            log.exception("sync failed")
            self._schema_ready = False
            with self._lock:
                self._status.last_error = f"{type(exc).__name__}: {exc}"
            return False
        with self._lock:
            self._status.last_success = datetime.now().astimezone()
            self._status.last_error = None
        return True

    def _sync(self) -> None:
        rev = self.repo.fetch()
        with db.connect(self._conninfo) as conn:
            if not self._schema_ready:
                db.ensure_schema(conn, self.settings.reader_roles)
                self._schema_ready = True
            state = db.get_state(conn)
            if state.commit == rev.commit:
                db.touch(conn)
                log.log(
                    logging.DEBUG if self._reported else logging.INFO,
                    "registry up to date at %s (%d objects)",
                    rev.commit[:12],
                    state.object_count,
                )
            else:
                self.repo.checkout(rev.commit)
                started = time.monotonic()
                snap = load_registry(self.repo.path)
                if len(snap.objects) < self.settings.min_objects:
                    raise RuntimeError(
                        f"only {len(snap.objects)} objects in checkout "
                        f"(min {self.settings.min_objects}), refusing to import"
                    )
                db.import_snapshot(conn, snap, rev.commit, rev.commit_time)
                log.info(
                    "imported %s -> %s: %d objects, %d skipped, %.1fs",
                    state.commit[:12] if state.commit else "(empty)",
                    rev.commit[:12],
                    len(snap.objects),
                    snap.errors,
                    time.monotonic() - started,
                )
                self._imports += 1
                if self._imports % self.settings.gc_every == 0:
                    self.repo.gc()
            state = db.get_state(conn)
        self._reported = True
        with self._lock:
            self._status.commit = state.commit
            self._status.commit_time = state.commit_time
            self._status.synced_at = state.synced_at
            self._status.object_count = state.object_count

    def run_forever(self) -> None:
        log.info(
            "registry sync started: %s (%s), every %ss",
            self.settings.repo_url,
            self.settings.branch,
            self.settings.sync_interval,
        )
        while not self._stop.is_set():
            self._trigger.clear()
            ok = self.run_once()
            self._heartbeat = time.monotonic()
            delay = self.settings.sync_interval if ok else self.settings.retry_interval
            self._trigger.wait(delay)
        log.info("registry sync stopped")
