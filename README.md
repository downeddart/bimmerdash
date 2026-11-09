# bimmerdash

Self-hosted logger and dashboard for BMW cars, built on BMW's official **CarData**
streaming API. In the spirit of TeslaMate / RiviAmigo, but for BMWs, including
combustion cars.

> **Status: early.** bimmerdash signs in to BMW, records the CarData stream into
> TimescaleDB and fetches your car's details and picture. Trip/fill-up detection, fuel cost
> analytics and the web UI are in progress.

Not affiliated with or endorsed by BMW AG. "BMW" is a trademark of BMW AG.

## Roadmap

- [x] BMW sign-in (device code flow; your password never touches bimmerdash)
- [x] Record the raw MQTT stream to disk
- [x] `keys` command to see which data points your car sends
- [x] PostgreSQL/TimescaleDB storage (every data point, any key)
- [x] Vehicle details and BMW's render of your car (2 API calls, refreshed monthly)
- [ ] Trips, fill-ups and fuel economy
- [ ] Fuel cost via the UK Fuel Finder price data
- [ ] Mileage and usage analytics (annual projection, when/where you drive)
- [ ] Web UI (dark, mobile-friendly)
- [ ] Pushover alerts (left unlocked, low fuel, service due, stream silent)

## Quick start

Requires Docker with Compose, and a BMW account in a market that offers CarData.

1. Set up CarData in the BMW portal: [docs/bmw-portal-setup.md](docs/bmw-portal-setup.md)
2. Configure:
   ```sh
   cp .env.example .env
   # set BIMMERDASH_CLIENT_ID, and a database password:
   echo "BIMMERDASH_DB_PASSWORD=$(openssl rand -hex 24)" >> .env
   ```
3. Sign in. A link and code appear; approve it in your browser:
   ```sh
   docker compose run --rm collector login
   ```
4. Start recording:
   ```sh
   docker compose up -d
   docker compose logs -f collector
   ```
5. After some driving, see what your car reports:
   ```sh
   docker compose run --rm collector keys
   docker compose run --rm collector keys --redact   # values hidden, safe to share in issues
   ```

Images are published to `ghcr.io/downeddart/bimmerdash-collector` (amd64 and arm64).
Pin a version with `BIMMERDASH_VERSION` in `.env`.

### Other commands

| Command | What it does |
| --- | --- |
| `import-raw` | Load recorded JSONL files into the database (safe to re-run) |
| `fetch-vehicle` | Re-fetch vehicle details and picture now (2 API calls) |
| `migrate` | Create/upgrade the database schema (the collector does this on start) |

BMW allows 50 CarData API calls per day. bimmerdash logs every call and stops at 45.

## Privacy

Everything stays on your machine. The data volume holds your BMW tokens, VIN and GPS
history. Back it up, and **don't paste raw data or unredacted logs into public issues**.

## Development

```sh
cd collector
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest && ruff check .
```

Database tests run when `BIMMERDASH_TEST_DATABASE_URL` points at a disposable TimescaleDB.
To run the stack from source, use the dev override:

```sh
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

Install [pre-commit](https://pre-commit.com) (`pre-commit install`) so gitleaks checks
every commit for secrets.

## License

GPL-3.0-only. See [LICENSE](LICENSE).
