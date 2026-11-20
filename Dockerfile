# syntax=docker/dockerfile:1.7
FROM python:3.13-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt \
    && useradd --uid 10001 --create-home factorlab \
    && mkdir -p /codex-home && chown factorlab /codex-home
COPY backend/src ./src
COPY backend/migrations ./migrations
COPY data ./data
ENV PYTHONPATH=/app/src \
    MIGRATIONS_DIR=/app/migrations \
    DEALS_SOURCE=/app/data/deals.csv \
    INDICATORS_CSV=/app/data/indicators.csv \
    CODEX_HOME=/codex-home
USER factorlab
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4)"
CMD ["uvicorn", "factor_lab.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
