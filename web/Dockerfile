# --- frontend build ---
# Static output is the same on every CPU, so build it natively on the build machine
# (not under emulation) even when the final image targets arm64.
FROM --platform=$BUILDPLATFORM node:22-alpine@sha256:1314fb30ec37592d5845e5a160d522c0e680972aa534549940b8d73bde88481a AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- runtime ---
# Base images pinned by digest; Dependabot proposes updates.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --create-home --uid 1000 app
WORKDIR /app
COPY pyproject.toml requirements.txt ./
# Dependencies from the hash-locked file only, then the app itself without resolving again.
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY src ./src
COPY --from=frontend /fe/dist ./src/bimmerdash_web/static
RUN pip install --no-cache-dir --no-deps .
USER app
EXPOSE 8320
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"BIMMERDASH_WEB_PORT\",\"8320\")}/api/health', timeout=4)"
ENTRYPOINT ["bimmerdash-web"]
