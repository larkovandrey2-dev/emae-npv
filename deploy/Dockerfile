FROM rust:1.90.0-bookworm@sha256:3914072ca0c3b8aad871db9169a651ccfce30cf58303e5d6f2db16d1d8a7e58f AS engine
RUN apt-get update && apt-get install -y --no-install-recommends python3 && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY engine.lock.json ./engine.lock.json
COPY scripts/bootstrap_engine.py ./scripts/bootstrap_engine.py
RUN python3 scripts/bootstrap_engine.py

FROM node:22-bookworm-slim@sha256:4b150b48b4116e2a22f2c39cc27025fbf4f153e2078fd3b14d159fdb5c7279da AS frontend
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim-bookworm@sha256:281d39ff02e3b389401586134ff9e6572954687822a58055ec07a60ded4e0b7f AS app
WORKDIR /app
COPY backend/requirements.lock.txt ./backend/requirements.lock.txt
RUN pip install --no-cache-dir -r backend/requirements.lock.txt && useradd --uid 10001 --create-home factorlab
COPY --from=engine /build/.engine/cashflow-rs/target/release/cfcalc ./.engine/cashflow-rs/target/release/cfcalc
COPY --from=engine /build/.engine/cashflow-rs/data/cbkey1m.csv ./.engine/cashflow-rs/data/cbkey1m.csv
COPY --from=engine /build/.engine/build-info.json ./.engine/build-info.json
COPY engine.lock.json ./
COPY data/fixtures ./data/fixtures
COPY backend/app ./backend/app
COPY --from=frontend /build/frontend/dist ./frontend/dist
RUN mkdir -p /app/.runtime && chown -R factorlab:factorlab /app/.runtime
USER factorlab
EXPOSE 8000
HEALTHCHECK --interval=20s --timeout=3s CMD python -c "import json, urllib.request; assert json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/health'))['engine_ready']"
CMD ["python", "-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000"]
