# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Primarily the maintainer: one owner of one petrol BMW in the UK, running bimmerdash on
a home server and opening it on the home network. Other BMW owners can self-host it
from the public repo and are welcome, but their needs do not drive decisions; features
are built for the maintainer's car first and kept generic where that is cheap.

The owner uses it two ways, about equally:

- **Glances on a phone:** how much fuel is left and where it is cheapest nearby, whether
  the car is locked, whether anything needs attention (service, tyres, warning lights).
- **Sit-down review on a desktop:** trips on a map, fuel economy and costs over time,
  mileage against a yearly limit, where and when the car is driven.

## Product Purpose

A self-hosted logger and dashboard for BMW cars, in the spirit of TeslaMate and
RiviAmigo but for BMWs, including combustion cars. It records everything the car sends
through BMW's official CarData API and turns it into answers about the car.

Priorities, in order:

1. Fuel economy and cost
2. Mileage and usage
3. Car health

Success: the owner trusts its numbers enough to stop keeping fuel receipts or a
spreadsheet, and hears about a problem (left unlocked, low tyre, service due) before
they would have noticed it themselves.

## Positioning

Built on BMW's official CarData streaming and REST APIs rather than scraping the
MyBMW app, so it works for combustion BMWs that EV-focused loggers ignore, and
records the full raw stream so new analyses can be run over old data. Fuel costs use
live UK station prices (Fuel Finder) rather than a fixed figure.

## Operating Context

- Runs as Docker Compose services (collector, prices, web, TimescaleDB) on a home
  server; the web UI is reached on the LAN, behind a single-account login.
- Data arrives continuously over BMW's MQTT stream while the car is used, plus a REST
  poll every few hours (limited to 50 BMW API calls per day) for service, warnings and
  counters the stream does not send.
- Alerts are delivered by Pushover when keys are configured, otherwise shown in the UI.
- History starts when the owner starts collecting; there is no importer for older data.

## Capabilities and Constraints

- Pages: Overview, Trips, Fuel, Insights, Health, Data explorer, Settings.
- Units are user settings: miles or km, mpg or L/100 km, fuel grade (E10, E5, diesel).
  UK conventions by default (miles, mpg, £, pence per litre).
- Any figure based on averages rather than the owner's own data (e.g. UK average fuel
  price) must be visibly marked as an estimate, never shown as exact.
- Prices and other time-sensitive reference data are always fetched live, never
  shipped as static values that go stale.
- What the car reports varies by model and market; every view must cope with a value
  being missing or not yet reported.
- Open decisions: support for markets outside the UK (fuel prices are UK-only today).

## Brand Commitments

- Name: bimmerdash. Not affiliated with or endorsed by BMW AG; BMW marks are not used
  as branding.
- Dark UI in the manner of RiviAmigo; explicitly not the look of a Grafana dashboard.
- Visual direction is the category standard for a car logger, executed at full craft
  (owner's choice, 2026-11-11, after two rounds of alternative visual worlds). The bar
  is RiviAmigo for the category, the Tesla and Rivian owner apps for the phone glance
  (car first, one status, what needs attention), and Linear for dense desktop screens.
  Familiar beats novel; polish, not reinvention.
- Must not imitate BMW's own apps or branding.

## Evidence on Hand

- The car's own image comes from BMW's vehicle render API at runtime; it is not stored
  in the repo.
- No screenshots, testimonials, users or usage statistics exist. Do not invent them.

## Product Principles

1. **Privacy first.** The repo is public: nothing that identifies a vehicle or person
   (VIN, account IDs, tokens, real locations) is ever committed, and the app sends
   nothing anywhere the owner did not configure.
2. **Honest numbers.** Prefer the car's own measurements, show where a figure comes
   from, and mark estimates as estimates.
3. **Answers, not telemetry.** Lead with what the owner wants to know (cost, range,
   what needs doing) and keep raw data one step away for those who want it.
4. **Quiet until it matters.** Alerts fire once, clear themselves, and are tunable.
