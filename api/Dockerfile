# syntax=docker/dockerfile:1.7
# One image runs the api, worker, scheduler and streamer; only the command differs.

ARG PYTHON_IMAGE=python:3.12-slim-bookworm

FROM ${PYTHON_IMAGE} AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir uv==0.12.23
WORKDIR /app

# Development: every dependency group (tests, lint, types). Source is bind-mounted over /app;
# the virtualenv lives in /opt/venv so the mount never hides it.
FROM base AS dev
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked
COPY . .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# Production: runtime dependencies only, running as a non-root user.
FROM base AS prod
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
# /data/backtests is the backtest tape cache (a volume in production; created here so the volume
# starts out owned by the app user). Matplotlib needs a writable config dir for its font cache.
RUN useradd --system --uid 10001 --no-create-home breakout \
    && mkdir -p /data/backtests && chown breakout /data/backtests
ENV BACKTEST_DIR=/data/backtests \
    MPLCONFIGDIR=/tmp/matplotlib
USER breakout
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
