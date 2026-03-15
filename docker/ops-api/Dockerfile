FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

WORKDIR /app

ENV PATH="/app/.venv/bin:${PATH}" \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

RUN apt-get update \
    && apt-get install -y --no-install-recommends docker.io \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
COPY apps ./apps
COPY packages ./packages

RUN uv sync --frozen --no-dev --package mas-ops-api

EXPOSE 8080

CMD ["mas-ops-api", "serve", "--host", "0.0.0.0", "--port", "8080"]
