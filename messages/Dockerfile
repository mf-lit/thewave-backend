FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# curl serves the container's healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev

COPY src/ ./src/

EXPOSE 5005

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SQLITE_DB_PATH=data/messages.db \
    UPSTREAM_DB_PATH=/data/upstream-api/water_temperature.db

# One entry point: this service has no worker. Messages are authored through
# the admin API and served on request, so nothing runs on a timer.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:5005/health || exit 1

CMD ["uv", "run", "gunicorn", \
     "--workers", "2", \
     "--bind", "0.0.0.0:5005", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "src.wsgi:app"]
