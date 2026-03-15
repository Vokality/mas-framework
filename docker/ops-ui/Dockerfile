FROM oven/bun:1.2.15

WORKDIR /app/apps/mas-ops-ui

COPY apps/mas-ops-ui/package.json apps/mas-ops-ui/bun.lock ./
RUN bun install --frozen-lockfile

COPY apps/mas-ops-ui ./

ARG VITE_MAS_OPS_API_BASE_URL=/api
ENV VITE_MAS_OPS_API_BASE_URL=${VITE_MAS_OPS_API_BASE_URL}

RUN bun run build

EXPOSE 4173

CMD ["bun", "run", "preview", "--", "--host", "0.0.0.0", "--port", "4173"]
