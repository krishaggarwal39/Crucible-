FROM python:3.12-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Copy dependency files and README for hatchling
COPY pyproject.toml uv.lock README.md ./

# Install dependencies
RUN uv pip install --system -e .

# Copy application code and tests
COPY src/ src/
COPY tests/ tests/
COPY alembic.ini ./

# Set environment
ENV PYTHONPATH=/app/src
