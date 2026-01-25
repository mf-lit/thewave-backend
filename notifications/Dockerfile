FROM python:3.11-slim

# Install UV package manager
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Set working directory
WORKDIR /app

# Copy dependency files
COPY pyproject.toml uv.lock* ./

# Install dependencies using UV
RUN uv sync --frozen --no-dev

# Install gunicorn for production WSGI server (in UV environment)
RUN uv run pip install gunicorn

# Copy application code
COPY src/ ./src/
# COPY config/ ./config/

# Create data directory for SQLite database
# RUN mkdir -p data

# Copy startup script
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

# Expose API port
EXPOSE 5001

# Set environment variables defaults
ENV PYTHONUNBUFFERED=1
ENV CALENDAR_API_URL=http://thewave-upstream-api:5000/calendar
ENV SQLITE_DB_PATH=data/notifications.db
ENV FLASK_ENV=production

# Use the startup script as entrypoint
ENTRYPOINT ["./docker-entrypoint.sh"]
