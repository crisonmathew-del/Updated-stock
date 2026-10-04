"""FastAPI application entrypoint. Run with `uvicorn app.main:app`."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from app import __version__
from app.api.routes import health
from app.core.config import get_settings
from app.core.db import get_engine
from app.core.logging import configure_logging, get_logger
from app.core.redis import get_redis

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    log.info("api.startup", env=settings.app_env, version=__version__)
    yield
    await get_redis().aclose()
    await get_engine().dispose()
    log.info("api.shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)

    app = FastAPI(
        title="Breakout API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )

    api = APIRouter(prefix="/api")
    api.include_router(health.router)
    app.include_router(api)
    return app


app = create_app()
