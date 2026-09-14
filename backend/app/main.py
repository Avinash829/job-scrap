"""FastAPI application factory.

Read-only by design: ingestion runs in a separate scheduled process (GitHub
Actions), so this container can sleep on a free tier without losing data.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.core.config import get_settings
from app.db.session import init_db
from app.api.v1.routes import jobs

log = logging.getLogger(__name__)

health_router = APIRouter(tags=["meta"])


@health_router.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
def root() -> dict:
    """Answer the bare URL instead of 404ing.

    Render probes `HEAD /` when a deploy goes live, and a person opening the
    API URL in a browser lands here too - both used to get a 404.
    """
    return {"service": "jobscrap-api", "docs": "/docs", "health": "/health", "jobs": "/api/v1/jobs"}


@health_router.get("/health")
def health() -> dict:
    """Liveness only - deliberately does not touch the database.

    Render pings this constantly; waking a suspended Neon compute on every
    ping would burn the free tier's compute hours for nothing.
    """
    s = get_settings()
    return {
        "status": "ok",
        "llm_configured": s.llm_enabled,
        "llm_keys": len(s.gemini_api_keys),
        "db": s.database_url.split("://", 1)[0],
    }


@health_router.get("/ready")
def ready() -> dict:
    """Readiness - actually queries the DB. Use this to verify a deploy."""
    from sqlalchemy import text

    from app.db.session import get_engine

    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ready"}


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    log.info("jobscrap api ready")
    yield


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="jobscrap API",
        version="0.1.0",
        description="Remote-first job aggregation, filtered for early-career roles.",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_origin_regex=settings.cors_origin_regex or None,
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    # descriptions compress well; listings are mostly text
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    app.include_router(health_router)
    app.include_router(jobs.router, prefix="/api/v1")
    return app


app = create_app()
