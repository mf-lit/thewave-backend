FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# curl serves the API container's healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev

COPY src/ ./src/

EXPOSE 5001

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CALENDAR_API_URL=http://thewave-upstream-api:5000/calendar \
    SQLITE_DB_PATH=data/notifications.db

# One image, two entry points: this is the API, and the worker service
# overrides it with `command:`. Neither process supervises the other any
# more — that is the container runtime's job.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:5001/health || exit 1

CMD ["uv", "run", "gunicorn", \
     "--workers", "2", \
     "--bind", "0.0.0.0:5001", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "src.wsgi:app"]
