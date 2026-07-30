FROM python:3.12-slim

WORKDIR /app

# curl is needed for the container healthcheck
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Copy dependency files and README for hatchling
COPY pyproject.toml uv.lock README.md ./

# Install dependencies
RUN uv pip install --system -e .

# Copy application code (tests are deliberately NOT shipped in the runtime image)
COPY src/backend/ src/backend/
COPY alembic.ini ./

# Set environment
ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1

# Run as an unprivileged user
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# --no-proxy-headers: uvicorn would otherwise rewrite request.client from
# X-Forwarded-For (its default is ON), overriding the application's
# TRUSTED_PROXY_COUNT policy. The app decides whether to trust that header.
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
