"""FastAPI application entrypoint. Run with `uvicorn app.main:app`."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint

from app import __version__
from app.api.routes import admin, auth, health, market, patterns, stocks
from app.api.routes import settings as settings_routes
from app.core.config import get_settings
from app.core.db import get_engine
from app.core.logging import configure_logging, get_logger
from app.core.queue import close_queue
from app.core.redis import get_redis

log = get_logger(__name__)

# Unsafe requests must carry this header. Browsers only let same-origin scripts set custom
# headers (we never enable CORS), so a cross-site form or image can't forge a request.
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "breakout"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


async def require_csrf_header(request: Request, call_next: RequestResponseEndpoint) -> Response:
    if request.method in UNSAFE_METHODS and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        return JSONResponse(
            {"detail": f"Missing {CSRF_HEADER}: {CSRF_VALUE} header."}, status_code=403
        )
    return await call_next(request)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    log.info("api.startup", env=settings.app_env, version=__version__)
    yield
    await close_queue()
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

    app.middleware("http")(require_csrf_header)

    api = APIRouter(prefix="/api")
    api.include_router(health.router)
    api.include_router(auth.router)
    api.include_router(settings_routes.router)
    api.include_router(admin.router)
    api.include_router(market.router)
    api.include_router(stocks.router)
    api.include_router(patterns.router)
    app.include_router(api)
    return app


app = create_app()
