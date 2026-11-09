# --- frontend build ---
FROM node:22-alpine AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- runtime ---
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --create-home --uid 1000 app
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY --from=frontend /fe/dist ./src/bimmerdash_web/static
RUN pip install --no-cache-dir .
USER app
EXPOSE 8320
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"BIMMERDASH_WEB_PORT\",\"8320\")}/api/health', timeout=4)"
ENTRYPOINT ["bimmerdash-web"]
