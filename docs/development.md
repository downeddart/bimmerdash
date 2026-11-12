# Development

## Running from source

```sh
cd collector
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest && ruff check .
```

The web UI's API lives in `web/` (FastAPI, same steps). The frontend (Svelte 5 + Vite)
lives in `web/frontend`: `npm run dev` proxies `/api` to a running bimmerdash-web on
port 8320.

Database tests run when `BIMMERDASH_TEST_DATABASE_URL` points at a disposable
TimescaleDB. They drop and recreate their databases, so never point it at real data.

To run the whole stack from source, use the dev override:

```sh
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

Install [pre-commit](https://pre-commit.com) (`pre-commit install`) so gitleaks checks
every commit for secrets. Tests and screenshots use made-up VINs and coordinates only.

## Layout

| Path | What |
| --- | --- |
| `collector/` | Signs in to BMW, records the stream, polls the REST API, derives trips and fill-ups, fetches fuel prices, sends alerts. Owns the database schema (`migrations/`). |
| `web/` | The web UI: a FastAPI app serving the API and the built Svelte frontend. |
| `docs/` | BMW portal setup, screenshots, this file. |

## Updating dependencies

Images install Python packages only from the hash-locked `collector/requirements.txt`
and `web/requirements.txt`, and use base images pinned by digest. Dependabot proposes
updates weekly; Python and Node version upgrades for the images are done by hand. To
change a Python dependency, edit `pyproject.toml` and regenerate the lock in the same
folder:

```sh
pip install "pip-tools==7.4.1" "click<8.2"
pip-compile --allow-unsafe --generate-hashes --strip-extras -o requirements.txt pyproject.toml
```

## Upgrading TimescaleDB

A new TimescaleDB image keeps running the old extension version on an existing database
until it is updated. After pulling a new `db` image, back up, stop the other services so
nothing else is connected, and update:

```sh
docker compose exec db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > backup.dump
docker compose stop collector prices web
docker compose exec db sh -c 'psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "ALTER EXTENSION timescaledb UPDATE"'
docker compose start collector prices web
```
