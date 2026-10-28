FROM node:22-bookworm-slim AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.8.8 AS uv-bin

FROM python:3.12-slim AS backend-build
COPY --from=uv-bin /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim AS runtime
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AUCTION_DB_PATH=/app/data/auction.sqlite3 \
    AUCTION_FRONTEND_DIR=/app/frontend/dist
RUN groupadd --system auction \
    && useradd --system --gid auction --home-dir /app auction \
    && mkdir -p /app/data \
    && chown auction:auction /app/data
COPY --from=backend-build /app/.venv /app/.venv
COPY backend/ ./backend/
COPY --from=frontend-build /build/frontend/dist ./frontend/dist/
USER auction
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)" || exit 1
CMD ["uvicorn", "backend.auction_sim.main:app", "--host", "0.0.0.0", "--port", "8000"]
