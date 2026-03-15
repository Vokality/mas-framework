FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

WORKDIR /app

ENV PATH="/app/.venv/bin:${PATH}" \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

COPY pyproject.toml uv.lock ./
COPY apps ./apps
COPY packages ./packages

RUN uv sync --frozen --no-dev --package mas-runtime

EXPOSE 50051

CMD ["mas-runtime", "run", "--config-file", "/runtime/mas.yaml"]
