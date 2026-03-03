# Use Python 3.12 slim image as base
FROM python:3.12-slim

# Install system dependencies and create non-root user
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    curl && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd -r appuser && \
    useradd -r -g appuser appuser

# Set working directory
WORKDIR /app

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies system-wide using uv
RUN uv pip install --system --no-cache .

# Copy application code
COPY main.py ./
COPY src/ ./src/
# COPY config/ ./config/
# COPY data/ ./data/

# Change ownership to non-root user
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose the port the app runs on
EXPOSE 5000

# Set environment variables for production
ENV PORT=5000
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV FLASK_ENV=production

# Health check
# HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    # CMD curl -f http://localhost:5000/water-temperature || exit 1

# Run the application using gunicorn (production WSGI server)
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "2", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-", "main:app"]

