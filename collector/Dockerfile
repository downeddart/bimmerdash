FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 BIMMERDASH_DATA_DIR=/data

RUN useradd --create-home --uid 1000 app && mkdir /data && chown app:app /data

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

USER app
VOLUME ["/data"]
ENTRYPOINT ["bimmerdash"]
CMD ["stream"]
