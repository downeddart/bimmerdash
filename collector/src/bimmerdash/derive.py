"""Trips and fill-ups, derived from stored telemetry.

The stream sends a burst of data points whenever something changes (doors, a few
minutes into a drive, the end of a drive). There is no explicit "trip" message, so:

* A **session** runs from the car being opened to it being locked from outside
  (door status ``SECURED``), or until the data goes quiet. While driving the car
  auto-locks to ``LOCKED``, which is not ``SECURED``, so a drive stays in one session.
* A session is a **trip** if the odometer moved by at least 1 km or the car's position
  moved noticeably.
* A **fill-up** is a jump in fuel level between two readings. Where it happened is found
  by matching positions reported in that window against UK Fuel Finder forecourts.

Everything is rebuilt from telemetry each time, so improving these rules (or importing
older data) corrects past results too.
"""

from __future__ import annotations

import bisect
import logging
import math
import os
import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from psycopg.types.json import Jsonb

log = logging.getLogger(__name__)

ODOMETER = "vehicle.vehicle.travelledDistance"
DOORS = "vehicle.cabin.door.status"
LATITUDE = "vehicle.cabin.infotainment.navigation.currentLocation.latitude"
LONGITUDE = "vehicle.cabin.infotainment.navigation.currentLocation.longitude"
FUEL_LITRES = "vehicle.drivetrain.fuelSystem.remainingFuel"
FUEL_PERCENT = "vehicle.drivetrain.fuelSystem.level"
KEYS = [ODOMETER, DOORS, LATITUDE, LONGITUDE, FUEL_LITRES, FUEL_PERCENT]

PARKED = "SECURED"  # locked from outside; LOCKED alone also happens while driving
IDLE_GAP = timedelta(minutes=45)  # no data for this long ends a session
MOVE_M = 300  # position change that counts as a trip even if the odometer didn't tick
FILL_MIN_LITRES = 4.0
FILL_MIN_PERCENT = 7.0
FILL_MERGE = timedelta(minutes=45)  # jumps this close together are one fill-up
STATION_RADIUS_M = 150
PRICE_RANGE = (80.0, 260.0)  # pence/litre; Fuel Finder has the odd typo (e.g. 299.9)

# Fuel Finder grade for each kind of fuel, and the DESNZ average series it falls back to.
DEFAULT_GRADE = {"petrol": "E10", "diesel": "B7_STANDARD"}
GRADE_KIND = {
    "E10": "petrol",
    "E5": "petrol",
    "B7_STANDARD": "diesel",
    "B7_PREMIUM": "diesel",
    "B10": "diesel",
    "HVO": "diesel",
}


@dataclass(frozen=True)
class Position:
    time: datetime
    lat: float
    lon: float


@dataclass
class Trip:
    start: datetime
    end: datetime
    start_odometer: float | None
    end_odometer: float | None
    distance_km: float
    start_pos: Position | None
    end_pos: Position | None
    route: list[Position] = field(default_factory=list)
    fuel_start_l: float | None = None
    fuel_end_l: float | None = None

    @property
    def fuel_used_l(self) -> float | None:
        if self.fuel_start_l is None or self.fuel_end_l is None:
            return None
        used = self.fuel_start_l - self.fuel_end_l
        return used if used >= 0 else None


@dataclass
class FuelReading:
    time: datetime
    litres: float | None
    percent: float | None
    litres_from_percent: bool = False


@dataclass
class FillUp:
    time: datetime
    before: FuelReading
    after: FuelReading
    litres_added: float | None
    position: Position | None = None
    station: dict | None = None  # nearest forecourt, when one matched
    odometer: float | None = None


# --- small helpers -----------------------------------------------------------------


def haversine_m(a: Position, b: Position) -> float:
    r = 6371000.0
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp, dl = p2 - p1, math.radians(b.lon - a.lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


class Series:
    """Time-ordered values of one key, with 'value at or before t' lookups."""

    def __init__(self, points: list[tuple[datetime, object]]):
        points = sorted(points, key=lambda p: p[0])
        self.times = [p[0] for p in points]
        self.values = [p[1] for p in points]

    def __len__(self) -> int:
        return len(self.times)

    def at(self, t: datetime, not_before: datetime | None = None):
        """Latest (time, value) at or before t, optionally no older than not_before."""
        i = bisect.bisect_right(self.times, t) - 1
        if i < 0 or (not_before is not None and self.times[i] < not_before):
            return None
        return self.times[i], self.values[i]

    def first_after(self, t: datetime, before: datetime | None = None):
        i = bisect.bisect_left(self.times, t)
        if i >= len(self.times) or (before is not None and self.times[i] >= before):
            return None
        return self.times[i], self.values[i]

    def between(self, start: datetime, end: datetime) -> list[tuple[datetime, object]]:
        i = bisect.bisect_left(self.times, start)
        j = bisect.bisect_right(self.times, end)
        return list(zip(self.times[i:j], self.values[i:j], strict=True))


def pair_positions(lat: list[tuple], lon: list[tuple], tolerance_s: float = 10) -> list[Position]:
    """Latitude and longitude arrive as separate data points; join them by timestamp."""
    lons = Series(lon)
    out = []
    for t, la in lat:
        hit = lons.at(t + timedelta(seconds=tolerance_s), t - timedelta(seconds=tolerance_s))
        if hit is not None and la is not None and hit[1] is not None:
            out.append(Position(t, float(la), float(hit[1])))
    return out


# --- trips -------------------------------------------------------------------------


@dataclass(frozen=True)
class Session:
    start: datetime
    end: datetime


def sessions(timeline: list[datetime], doors: Series, now: datetime) -> list[Session]:
    """Split event times into open-car sessions. A still-running session is left out."""
    out: list[Session] = []
    start = last = None
    for t in timeline:
        status = doors.at(t)
        parked = status is not None and status[1] == PARKED
        if start is not None and t - last > IDLE_GAP:
            out.append(Session(start, last))
            start = None
        if start is None and not parked:
            start = t
        elif start is not None and parked:
            out.append(Session(start, t))
            start = None
        last = t
    if start is not None and now - last > IDLE_GAP:
        out.append(Session(start, last))
    return out


def detect_trips(
    odometer: Series,
    doors: Series,
    positions: list[Position],
    fuel: list[FuelReading],
    now: datetime,
) -> list[Trip]:
    pos = Series([(p.time, p) for p in positions])
    litres = Series([(r.time, r.litres) for r in fuel if r.litres is not None])
    timeline = sorted(set(odometer.times) | set(doors.times) | set(pos.times))
    found = sessions(timeline, doors, now)
    trips = []
    for i, s in enumerate(found):
        prev_end = found[i - 1].end if i else None
        next_start = found[i + 1].start if i + 1 < len(found) else None

        o_start = odometer.at(s.start) or odometer.first_after(s.start, s.end)
        o_end = odometer.at(s.end)
        km = (o_end[1] - o_start[1]) if o_start and o_end else 0.0

        p_start = pos.at(s.start) or pos.first_after(s.start, s.end)
        # The final position can trail the lock by a few seconds.
        p_end = pos.at(s.end + timedelta(minutes=2), s.start)
        moved_m = haversine_m(p_start[1], p_end[1]) if p_start and p_end else 0.0
        if km < 1 and moved_m < MOVE_M:
            continue

        route = [p for _, p in pos.between(s.start, s.end + timedelta(minutes=2))]
        # Fuel before: a reading taken while parked since the previous session.
        f0 = litres.at(
            s.start + timedelta(minutes=2), prev_end - timedelta(minutes=10) if prev_end else None
        )
        # Fuel after: the first reading once the drive has finished, before driving again.
        f1 = litres.first_after(s.end - timedelta(minutes=10), next_start)
        if f1 is not None and f1[0] > s.end + timedelta(hours=6):
            f1 = None
        if f1 is None:  # else the last reading taken during the drive
            f1 = litres.at(s.end, s.start)
        trips.append(
            Trip(
                start=s.start,
                end=s.end,
                start_odometer=o_start[1] if o_start else None,
                end_odometer=o_end[1] if o_end else None,
                distance_km=max(float(km), round(moved_m / 1000, 1)),
                start_pos=p_start[1] if p_start else None,
                end_pos=p_end[1] if p_end else None,
                route=route,
                fuel_start_l=f0[1] if f0 else None,
                fuel_end_l=f1[1] if f1 else None,
            )
        )
    return trips


# --- fill-ups ----------------------------------------------------------------------


def tank_capacity(readings: list[FuelReading]) -> float | None:
    """Usable tank size implied by litres vs percent, when both are reported."""
    ratios = [
        r.litres / (r.percent / 100)
        for r in readings
        if r.litres is not None and r.percent and r.percent >= 25 and not r.litres_from_percent
    ]
    return round(statistics.median(ratios), 1) if len(ratios) >= 3 else None


def fuel_readings(
    litres: list[tuple], percent: list[tuple], capacity: float | None
) -> list[FuelReading]:
    """Join litres and percent by timestamp. Litres fill in from percent if missing."""
    by_time: dict[datetime, FuelReading] = {}
    for t, v in litres:
        if v is not None:
            by_time.setdefault(t, FuelReading(t, None, None)).litres = float(v)
    for t, v in percent:
        if v is not None:
            by_time.setdefault(t, FuelReading(t, None, None)).percent = float(v)
    readings = [by_time[t] for t in sorted(by_time)]
    capacity = capacity or tank_capacity(readings)
    if capacity:
        for r in readings:
            if r.litres is None and r.percent is not None:
                r.litres = round(r.percent / 100 * capacity, 1)
                r.litres_from_percent = True
    return readings


def _jump(a: FuelReading, b: FuelReading) -> bool:
    if a.litres is not None and b.litres is not None:
        return b.litres - a.litres >= FILL_MIN_LITRES
    if a.percent is not None and b.percent is not None:
        return b.percent - a.percent >= FILL_MIN_PERCENT
    return False


def detect_fillups(
    readings: list[FuelReading],
    positions: list[Position],
    odometer: Series,
    find_stations: Callable[[float, float], list[dict]] | None = None,
) -> list[FillUp]:
    jumps: list[list[FuelReading]] = []
    for a, b in zip(readings, readings[1:], strict=False):
        if not _jump(a, b):
            continue
        if jumps and a.time - jumps[-1][1].time <= FILL_MERGE:
            jumps[-1][1] = b  # topped up again straight away: one fill-up
        else:
            jumps.append([a, b])

    pos = Series([(p.time, p) for p in positions])
    fills = []
    for before, after in jumps:
        added = (
            round(after.litres - before.litres, 1)
            if after.litres is not None and before.litres is not None
            else None
        )
        fill = FillUp(time=after.time, before=before, after=after, litres_added=added)
        # Where: any position reported between the two readings that sits on a forecourt.
        if find_stations is not None:
            best = None
            for _, p in pos.between(before.time, after.time + timedelta(minutes=2)):
                for st in find_stations(p.lat, p.lon)[:1]:
                    if best is None or st["distance_m"] < best[1]["distance_m"]:
                        best = (p, st)
            if best is not None:
                fill.position, fill.station = best
                fill.time = best[0].time
        o = odometer.at(fill.time)
        fill.odometer = o[1] if o else None
        fills.append(fill)
    return fills


# --- pricing -----------------------------------------------------------------------


def fuel_kind(basic_data: dict | None) -> str:
    p = str((basic_data or {}).get("propulsionType") or "").upper()
    return "diesel" if p in ("DI", "D", "DIESEL") else "petrol"


def price_fillup(fill: FillUp, grade: str, kind: str, station_price, uk_average) -> dict:
    """Pick the best price we have: the station's own, else the UK weekly average.

    station_price(node_id, grade, at) -> (pence, effective_at) | None
    uk_average(kind, on) -> (pence, week_start) | None
    """
    price = source = ref = None
    if fill.station is not None:
        found = station_price(fill.station["node_id"], grade, fill.time)
        if found and PRICE_RANGE[0] <= found[0] <= PRICE_RANGE[1]:
            price, ref, source = found[0], found[1], "station"
    if price is None:
        found = uk_average(kind, fill.time.date())
        if found:
            price, ref, source = found[0], found[1], "uk_average"
    cost = (
        round(fill.litres_added * price / 100, 2)
        if price is not None and fill.litres_added is not None
        else None
    )
    return {"price_ppl": price, "price_source": source, "price_ref": ref, "cost_gbp": cost}


# --- database rebuild --------------------------------------------------------------


def _load(conn, vin: str) -> dict[str, list[tuple]]:
    rows = conn.execute(
        "SELECT key, time, value, value_num FROM telemetry"
        " WHERE vin = %s AND key = ANY(%s) ORDER BY time",
        (vin, KEYS),
    ).fetchall()
    out: dict[str, list[tuple]] = {k: [] for k in KEYS}
    for key, t, value, num in rows:
        out[key].append((t, num if num is not None else value))
    return out


def _pos_json(p: Position | None):
    return None if p is None else [round(p.lat, 6), round(p.lon, 6)]


def rebuild(db, vin: str, now: datetime | None = None) -> tuple[int, int]:
    """Recompute trips and fill-ups for one car and replace the stored ones."""
    now = now or datetime.now(UTC)
    conn = db.conn
    data = _load(conn, vin)
    basic = conn.execute("SELECT basic_data FROM vehicles WHERE vin = %s", (vin,)).fetchone()
    kind = fuel_kind(basic[0] if basic else None)
    grade = os.environ.get("BIMMERDASH_FUEL_GRADE", "").strip().upper() or DEFAULT_GRADE[kind]
    kind = GRADE_KIND.get(grade, kind)
    capacity = float(os.environ.get("BIMMERDASH_TANK_LITRES", 0) or 0) or None
    conn.commit()

    odometer = Series([(t, float(v)) for t, v in data[ODOMETER] if v is not None])
    doors = Series(data[DOORS])
    positions = pair_positions(data[LATITUDE], data[LONGITUDE])
    fuel = fuel_readings(data[FUEL_LITRES], data[FUEL_PERCENT], capacity)

    trips = detect_trips(odometer, doors, positions, fuel, now)
    fills = detect_fillups(fuel, positions, odometer, db.nearest_forecourts)
    priced = [price_fillup(f, grade, kind, db.price_at, db.uk_average_at) for f in fills]

    with conn.transaction():
        conn.execute("DELETE FROM trips WHERE vin = %s", (vin,))
        conn.execute("DELETE FROM fillups WHERE vin = %s", (vin,))
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO trips (vin, start_time, end_time, start_odometer, end_odometer,"
                " distance_km, start_lat, start_lon, end_lat, end_lon, fuel_start_l,"
                " fuel_end_l, fuel_used_l, route)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        vin,
                        t.start,
                        t.end,
                        t.start_odometer,
                        t.end_odometer,
                        t.distance_km,
                        *(_pos_json(t.start_pos) or (None, None)),
                        *(_pos_json(t.end_pos) or (None, None)),
                        t.fuel_start_l,
                        t.fuel_end_l,
                        t.fuel_used_l,
                        Jsonb([[p.time.isoformat(), p.lat, p.lon] for p in t.route]),
                    )
                    for t in trips
                ],
            )
            cur.executemany(
                "INSERT INTO fillups (vin, time, odometer, litres_before, litres_after,"
                " percent_before, percent_after, litres_added, litres_estimated, lat, lon,"
                " node_id, station, distance_m, fuel_grade, fuel_kind, price_ppl, price_source,"
                " price_ref, cost_gbp)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
                " %s, %s, %s)",
                [
                    (
                        vin,
                        f.time,
                        f.odometer,
                        f.before.litres,
                        f.after.litres,
                        f.before.percent,
                        f.after.percent,
                        f.litres_added,
                        f.before.litres_from_percent or f.after.litres_from_percent,
                        *(_pos_json(f.position) or (None, None)),
                        f.station["node_id"] if f.station else None,
                        Jsonb(_station_json(f.station)) if f.station else None,
                        round(f.station["distance_m"]) if f.station else None,
                        grade,
                        kind,
                        p["price_ppl"],
                        p["price_source"],
                        str(p["price_ref"]) if p["price_ref"] is not None else None,
                        p["cost_gbp"],
                    )
                    for f, p in zip(fills, priced, strict=True)
                ],
            )
        conn.execute(
            "UPDATE vehicles SET derived_at = now(), tank_litres = %s, fuel_grade = %s,"
            " fuel_kind = %s WHERE vin = %s",
            (capacity or tank_capacity(fuel), grade, kind, vin),
        )
    return len(trips), len(fills)


def _station_json(st: dict) -> dict:
    return {k: st.get(k) for k in ("trading_name", "brand_name", "postcode", "lat", "lon")}


def rebuild_all(db, now: datetime | None = None) -> dict[str, tuple[int, int]]:
    vins = [r[0] for r in db.conn.execute("SELECT vin FROM vehicles").fetchall()]
    db.conn.commit()
    return {vin: rebuild(db, vin, now) for vin in vins}
