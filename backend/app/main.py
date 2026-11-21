import logging

from fastapi import FastAPI


class _HealthzAccessFilter(logging.Filter):
    """Drop uvicorn access log lines for the /healthz probe endpoint."""

    def filter(self, record: logging.LogRecord) -> bool:
        # uvicorn.access 的 args 格式：(client_addr, method, path, http_version, status_code)
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3:
            path = str(args[2]).split("?", 1)[0]
            return path != "/healthz"
        return True


logging.getLogger("uvicorn.access").addFilter(_HealthzAccessFilter())

app = FastAPI()


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
