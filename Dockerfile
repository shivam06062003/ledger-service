# ---- Stage 1: build a virtualenv with the app and its dependencies ----
FROM python:3.13-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY pyproject.toml ./
COPY app ./app
RUN pip install .

# ---- Stage 2: slim runtime image (no build tools, no source tree) ----
FROM python:3.13-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Never run as root inside the container.
RUN useradd --create-home --uid 1000 appuser

COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts/serve.sh ./serve.sh

USER appuser
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/live')"

# serve.sh starts uvicorn (one process per $WEB_CONCURRENCY) without its
# access log: our middleware already logs each request as structured JSON.
CMD ["./serve.sh"]
