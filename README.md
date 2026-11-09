# bimmerdash

Self-hosted logger and dashboard for BMW cars, built on BMW's official **CarData**
streaming API. In the spirit of TeslaMate / RiviAmigo, but for BMWs, including
combustion cars.

> **Status: early.** The current version signs in to BMW and records the raw CarData
> stream. Database, trip/fill-up detection, Grafana dashboards and alerts are coming.

Not affiliated with or endorsed by BMW AG. "BMW" is a trademark of BMW AG.

## Roadmap

- [x] BMW sign-in (device code flow; your password never touches bimmerdash)
- [x] Record the raw MQTT stream to disk
- [x] `keys` command to see which data points your car sends
- [ ] PostgreSQL/TimescaleDB storage
- [ ] Trips, fill-ups and fuel economy, service history
- [ ] Grafana dashboards
- [ ] Pushover alerts (left unlocked, low fuel, service due, stream silent)

## Quick start

Requires Docker with Compose, and a BMW account in a market that offers CarData.

1. Set up CarData in the BMW portal: [docs/bmw-portal-setup.md](docs/bmw-portal-setup.md)
2. Configure:
   ```sh
   cp .env.example .env   # then set BIMMERDASH_CLIENT_ID (and optionally BIMMERDASH_VIN)
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

Install [pre-commit](https://pre-commit.com) (`pre-commit install`) so gitleaks checks
every commit for secrets.

## License

GPL-3.0-only. See [LICENSE](LICENSE).
