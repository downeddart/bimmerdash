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
- [x] Car health: service schedule, MOT, tyre pressures against target, warning lights
- [x] Economy from the car's own fuel counters (to 0.1 L, not the tank gauge)
- [x] Refuel advisor: the cheapest station near the car, counting the drive there
- [x] Running cost projection, regular journeys, yearly mileage limit tracking
- [x] Alerts (left unlocked/open, low fuel, tyres, service due, warning lights), with Pushover
- [ ] Stream-silent alert

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
   Open it and create your account straight away (see [Signing in](#signing-in)).
   Keep it on your home network; for access from outside, put it behind a VPN or an HTTPS
   reverse proxy.
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
| `poll` | Fetch service, tyres, warnings and fuel use now (1 API call per car) |
| `migrate` | Create/upgrade the database schema (the collector does this on start) |

### How trips and fill-ups are detected

BMW doesn't send "trip" or "fill-up" events, so bimmerdash works them out:

- **Trip**: from opening the car until it is locked from outside, if the odometer moved
  at least 1 km or the car's position changed. Positions arrive every few minutes, so
  routes on the map are approximate.
- **Fill-up**: a jump of 4 L or more in the fuel level. If the car reported a position on
  a forecourt in between, the cost uses that station's price from UK Fuel Finder.
  Otherwise it uses the UK weekly average price (DESNZ), shown as **~£ / UK avg**.
  The car reports fuel in whole litres, so these costs can be out by a litre's worth.
  **Add receipt** on the Fuel page records what you actually paid (and litres, if you
  like); it then replaces the estimate and survives every recalculation.
- **Economy** comes from fuel burned between fill-ups, so partial fills work too.

Everything is recalculated from the stored data, so improvements apply to past trips.
For maps, your browser loads tiles from OpenStreetMap.

BMW allows 50 CarData API calls per day. bimmerdash logs every call and stops at 45.

### Health, economy and alerts

Some values never come over the stream: the service schedule, Check Control warnings and
the car's lifetime fuel counters. The collector asks BMW's REST API for them every 4 hours
(one call per car, so 6 a day; `BIMMERDASH_REST_POLL_HOURS` changes it, `0` turns it off).
The first time, it creates a *container* in your CarData account listing those keys.

- **Economy** is the change in the car's own fuel and distance counters between polls,
  so it's accurate to a tenth of a litre and doesn't need fill-ups.
- **Refuel advisor** (Fuel page): stations within reach of the car, ranked by the cost of
  filling up plus the fuel to drive there and back. It picks the nearest station unless
  another saves at least 50p.
- **Alerts** are listed on the Health page and, if you add Pushover keys to `.env`, sent to
  your phone: left unlocked or open (once the car has been quiet for 10 minutes), fuel
  low (with the cheapest station nearby), a tyre 10% below the car's target pressure, a
  service or MOT due within 30 days, and any warning light. Each is sent once, and again
  only after it has cleared. Turn them on and off in Settings.

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

Behind an HTTPS reverse proxy, tell bimmerdash so the session cookie is marked `Secure`
and failed logins are counted per real client: set `FORWARDED_ALLOW_IPS` on the `web`
service to the proxy's address (uvicorn then trusts its `X-Forwarded-*` headers), or set
`BIMMERDASH_COOKIE_SECURE=true` if you only need the cookie flag. Without either, every
client appears to come from the proxy and shares one failed-login counter.

### A separate database role for the web UI

Optional, recommended. The web UI only needs to write its own tables (login, sessions,
settings, places). Give it a role that can't do more:

```sh
echo "BIMMERDASH_WEB_DB_USER=bimmerdash_web" >> .env
echo "BIMMERDASH_WEB_DB_PASSWORD=$(openssl rand -hex 24)" >> .env
docker compose up -d
```

The collector creates the role and sets its password when it starts; the web UI then
connects with it. Without these two lines the UI uses the main role, as before.

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

### Updating dependencies

Images install Python packages only from the hash-locked `collector/requirements.txt`
and `web/requirements.txt`, and use base images pinned by digest. Dependabot proposes
updates weekly. To change a dependency by hand, edit `pyproject.toml` and regenerate the
lock in the same folder:

```sh
pip install "pip-tools==7.4.1" "click<8.2"
pip-compile --allow-unsafe --generate-hashes --strip-extras -o requirements.txt pyproject.toml
```

## License

GPL-3.0-only. See [LICENSE](LICENSE).
