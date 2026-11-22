import argparse
import logging
import signal
import sys
from types import FrameType

from .config import Settings
from .server import make_server, serve_in_background
from .worker import SyncWorker


def main() -> None:
    parser = argparse.ArgumentParser(prog="registry-sync")
    parser.add_argument(
        "--once", action="store_true", help="sync once and exit (non-zero on failure)"
    )
    args = parser.parse_args()

    settings = Settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    worker = SyncWorker(settings)

    if args.once:
        sys.exit(0 if worker.run_once() else 1)

    server = make_server(worker, settings.http_host, settings.http_port)
    serve_in_background(server)

    def _shutdown(signum: int, _frame: FrameType | None) -> None:
        logging.getLogger(__name__).info("received %s, shutting down", signal.Signals(signum).name)
        worker.stop()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    try:
        worker.run_forever()
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
