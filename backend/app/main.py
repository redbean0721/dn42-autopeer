import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import db
from app.api.public import registry
from app.config import Settings

PROBE_PATHS = {"/healthz", "/readyz"}


class _ProbeAccessFilter(logging.Filter):
    """Drop uvicorn access log lines for the probe endpoints."""

    def filter(self, record: logging.LogRecord) -> bool:
        # uvicorn.access 的 args 格式：(client_addr, method, path, http_version, status_code)
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3:
            path = str(args[2]).split("?", 1)[0]
            return path not in PROBE_PATHS
        return True


logging.getLogger("uvicorn.access").addFilter(_ProbeAccessFilter())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    engine = db.create_engine(Settings())
    app.state.engine = engine
    app.state.sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield
    finally:
        await engine.dispose()


app = FastAPI(lifespan=lifespan)
app.include_router(registry.router, prefix="/api")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz(request: Request) -> dict[str, str]:
    try:
        await db.ping(request.app.state.engine)
    except (SQLAlchemyError, OSError) as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ok"}
