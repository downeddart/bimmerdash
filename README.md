# bimmerdash

Self-hosted logger and dashboard for BMW cars, built on BMW's official **CarData**
streaming API. In the spirit of TeslaMate / RiviAmigo, but for BMWs, including
combustion cars.

> **Status: early.** bimmerdash signs in to BMW, records the CarData stream into
> TimescaleDB, fetches your car's details and picture, and shows them in a dark web UI
> with trips, detected fill-ups, fuel costs and local fuel prices.

Not affiliated with or endorsed by BMW AG. "BMW" is a trademark of BMW AG.

## Roadmap

- [x] BMW sign-in (device code flow; your password never touches bimmerdash)
- [x] Record the raw MQTT stream to disk
- [x] `keys` command to see which data points your car sends
- [x] PostgreSQL/TimescaleDB storage (every data point, any key)
- [x] Vehicle details and BMW's render of your car (2 API calls, refreshed monthly)
- [x] Trips, fill-ups and fuel economy
- [x] Fuel cost via the UK Fuel Finder price data (UK weekly averages where no station price)
- [x] Mileage and usage analytics (annual projection, when/where you drive, named places)
- [x] Web UI: overview with your car's picture, raw data explorer (dark, mobile-friendly)
- [x] Web UI: trips and fuel pages, with maps
- [x] Login (single account, first-run setup, password reset command)
- [x] Settings: miles or km, mpg or L/100 km, fuel grade (E10, E5, diesel)
- [x] Web UI: insights page, live map of the car on Overview
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
   The web UI is at `http://<your-server>:8320` (change with `BIMMERDASH_WEB_PORT`).
   It has no login yet: keep it on your home network or behind a reverse proxy with auth.
5. After some driving, see what your car reports:
   ```sh
   docker compose run --rm collector keys
   docker compose run --rm collector keys --redact   # values hidden, safe to share in issues
   ```

Images are published to `ghcr.io/downeddart/bimmerdash-collector` and
`ghcr.io/downeddart/bimmerdash-web` (amd64 and arm64).
Pin a version with `BIMMERDASH_VERSION` in `.env`.

### Other commands

| Command | What it does |
| --- | --- |
| `derive` | Rebuild trips and fill-ups from stored data (the collector does this every few minutes) |
| `import-raw` | Load recorded JSONL files into the database (safe to re-run) |
| `fetch-vehicle` | Re-fetch vehicle details and picture now (2 API calls) |
| `migrate` | Create/upgrade the database schema (the collector does this on start) |

### How trips and fill-ups are detected

BMW doesn't send "trip" or "fill-up" events, so bimmerdash works them out:

- **Trip**: from opening the car until it is locked from outside, if the odometer moved
  at least 1 km or the car's position changed. Positions arrive every few minutes, so
  routes on the map are approximate.
- **Fill-up**: a jump of 4 L or more in the fuel level. If the car reported a position on
  a forecourt in between, the cost uses that station's price from UK Fuel Finder.
  Otherwise it uses the UK weekly average price (DESNZ), shown as **~£ / UK avg**.
- **Economy** comes from fuel burned between fill-ups, so partial fills work too.

Everything is recalculated from the stored data, so improvements apply to past trips.
For maps, your browser loads tiles from OpenStreetMap.

BMW allows 50 CarData API calls per day. bimmerdash logs every call and stops at 45.

## Signing in

The web UI asks for a login. The first time you open it, it shows **Create your account**:
do that straight away, because until an account exists anyone who can reach the page can
create one. Or create it from the command line before opening the page:

```sh
docker compose exec web bimmerdash-web set-password <username>
```

The same command resets a forgotten password (and signs that account out everywhere).
Passwords are stored as scrypt hashes; sessions last 30 days. Repeated failed logins from
one address are blocked for 15 minutes.

If bimmerdash sits behind something that already requires a login (Authelia, Authentik,
Cloudflare Access...), you can set `BIMMERDASH_AUTH=off`. Don't turn it off otherwise:
there is no other protection.

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

Frontend (Svelte 5 + Vite) lives in `web/frontend`; `npm run dev` proxies `/api` to a
running bimmerdash-web on port 8320.

Database tests run when `BIMMERDASH_TEST_DATABASE_URL` points at a disposable TimescaleDB.
To run the stack from source, use the dev override:

```sh
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

Install [pre-commit](https://pre-commit.com) (`pre-commit install`) so gitleaks checks
every commit for secrets.

## License

GPL-3.0-only. See [LICENSE](LICENSE).
