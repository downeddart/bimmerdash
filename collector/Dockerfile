# Base image pinned by digest; Dependabot proposes updates.
FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

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
