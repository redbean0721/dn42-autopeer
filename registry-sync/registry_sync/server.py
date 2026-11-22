import json
import logging
import math
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .worker import SyncWorker

log = logging.getLogger(__name__)


def make_server(worker: SyncWorker, host: str, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        server_version = "registry-sync"

        def _send(
            self, code: int, body: dict[str, Any], headers: dict[str, str] | None = None
        ) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path == "/healthz":
                ok = worker.is_alive()
                self._send(200 if ok else 503, {"status": "ok" if ok else "stalled"})
            elif path == "/readyz":
                ok = worker.is_ready()
                self._send(200 if ok else 503, {"status": "ok" if ok else "not synced"})
            elif path == "/status":
                self._send(200, worker.status())
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path.split("?", 1)[0] != "/sync":
                self._send(404, {"error": "not found"})
                return
            wait = worker.request_sync()
            if wait > 0:
                retry = str(math.ceil(wait))
                self._send(
                    429, {"error": "cooldown", "retry_after": int(retry)}, {"Retry-After": retry}
                )
            else:
                self._send(202, {"status": "scheduled"})

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            if self.command != "GET":
                log.info("%s %s", self.address_string(), format % args)

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def serve_in_background(server: ThreadingHTTPServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, name="http", daemon=True)
    thread.start()
    return thread
