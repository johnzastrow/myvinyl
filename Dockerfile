# syntax=docker/dockerfile:1
# myvinyl container image: small, non-root, read-only friendly. Data lives in /data.

# --- Build: install locked dependencies and the app into a virtualenv ----------------------
FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.20 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
# Dependencies first so they cache between code changes.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

# --- Runtime -------------------------------------------------------------------------------
FROM python:3.13-slim
RUN groupadd --system --gid 10001 myvinyl \
 && useradd --system --uid 10001 --gid myvinyl --home-dir /data --shell /usr/sbin/nologin myvinyl \
 && mkdir -p /data \
 && chown myvinyl:myvinyl /data
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MYVINYL_DB=/data/myvinyl.db
USER myvinyl
WORKDIR /data
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]
CMD ["myvinyl", "--host", "0.0.0.0", "--port", "8000"]
