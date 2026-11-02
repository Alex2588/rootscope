# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

# Install uv by copying the official static binary rather than pip-installing it —
# keeps uv itself out of the project's own dependency graph.
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /usr/local/bin/

WORKDIR /app

# Install dependencies first, from the lockfile only, so this layer is cached across
# rebuilds that only change application code.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY app ./app

RUN uv sync --frozen --no-dev

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
