FROM python:3.13-slim

WORKDIR /app

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies (frozen = exact versions from lockfile)
RUN uv sync --frozen --no-dev

# Copy application code
COPY server.py ./
COPY mint/ ./mint/

EXPOSE 8000

CMD ["uv", "run", "python", "server.py"]
