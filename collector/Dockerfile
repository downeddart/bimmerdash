# Base image pinned by digest; Dependabot proposes updates.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 BIMMERDASH_DATA_DIR=/data

RUN useradd --create-home --uid 1000 app && mkdir /data && chown app:app /data

WORKDIR /app
COPY pyproject.toml requirements.txt ./
# Dependencies from the hash-locked file only, then the app itself without resolving again.
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY src ./src
RUN pip install --no-cache-dir --no-deps .

USER app
VOLUME ["/data"]
ENTRYPOINT ["bimmerdash"]
CMD ["stream"]
