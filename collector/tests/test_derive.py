from datetime import UTC, date, datetime, timedelta

from bimmerdash.derive import (
    FillUp,
    FuelReading,
    Position,
    Series,
    detect_fillups,
    detect_trips,
    fuel_kind,
    fuel_readings,
    pair_positions,
    price_fillup,
    sessions,
    tank_capacity,
)

DAY = datetime(2026, 11, 10, tzinfo=UTC)
HOME = (51.5000, -1.0000)  # fake coordinates
SHOP = (51.5100, -0.9800)
STATION = (51.5200, -0.9700)


def at(hhmm: str, s: int = 0) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return DAY + timedelta(hours=h, minutes=m, seconds=s)


# The shape of a real drive: unlock, auto-lock while driving, odometer ticks every
# few minutes, unlock on arrival, lock from outside (SECURED); later a second leg.
DOORS = [
    (DAY - timedelta(hours=16), "SECURED"),
    (at("12:15"), "UNLOCKED"),
    (at("12:16"), "LOCKED"),
    (at("12:19"), "LOCKED"),
    (at("12:22"), "LOCKED"),
    (at("12:23"), "UNLOCKED"),
    (at("12:24"), "SECURED"),
    (at("12:38"), "UNLOCKED"),
    (at("12:40"), "LOCKED"),
    (at("12:43"), "LOCKED"),
    (at("12:46"), "LOCKED"),
    (at("12:47"), "UNLOCKED"),
    (at("12:47", 30), "SECURED"),
    # moved the car on the drive: no odometer change, no real distance
    (at("12:57"), "UNLOCKED"),
    (at("12:58"), "SECURED"),
]
ODO = [
    (DAY - timedelta(hours=16), 106682.0),
    (at("12:15"), 106682.0),
    (at("12:19"), 106683.0),
    (at("12:22"), 106684.0),
    (at("12:24"), 106684.0),
    (at("12:38"), 106684.0),
    (at("12:43"), 106685.0),
    (at("12:46"), 106686.0),
    (at("12:47", 30), 106687.0),
    (at("12:58"), 106687.0),
]
POSITIONS = [
    Position(DAY - timedelta(hours=16), *HOME),
    Position(at("12:24"), *SHOP),
    Position(at("12:47", 30), *HOME),
    Position(at("12:58"), HOME[0] + 0.0001, HOME[1]),
]
NOW = at("18:00")


def test_sessions_split_on_lock_from_outside():
    doors = Series(DOORS)
    found = sessions(sorted(t for t, _ in DOORS), doors, NOW)
    assert [(s.start, s.end) for s in found] == [
        (at("12:15"), at("12:24")),
        (at("12:38"), at("12:47", 30)),
        (at("12:57"), at("12:58")),
    ]


def test_session_still_running_is_left_out():
    doors = Series(DOORS[:4])
    assert sessions(sorted(t for t, _ in DOORS[:4]), doors, at("12:20")) == []
    # ...but closes once the data has been quiet for a while
    assert len(sessions(sorted(t for t, _ in DOORS[:4]), doors, at("14:00"))) == 1


def test_detect_trips_from_real_drive_shape():
    fuel = [
        FuelReading(DAY - timedelta(hours=16), 24.0, 47.0),
        FuelReading(at("12:24"), 23.0, 45.0),
        FuelReading(at("12:48"), 22.0, 43.0),
    ]
    trips = detect_trips(Series(ODO), Series(DOORS), POSITIONS, fuel, NOW)
    assert len(trips) == 2  # the driveway shuffle is not a trip
    a, b = trips
    assert (a.start, a.end, a.distance_km) == (at("12:15"), at("12:24"), 2.0)
    assert (b.start_odometer, b.end_odometer, b.distance_km) == (106684.0, 106687.0, 3.0)
    assert (a.start_pos.lat, a.end_pos.lat) == (HOME[0], SHOP[0])
    assert (a.fuel_start_l, a.fuel_end_l, a.fuel_used_l) == (24.0, 23.0, 1.0)
    assert b.fuel_used_l == 1.0


def test_short_trip_fuel_after_is_not_the_reading_before():
    """A 9-minute trip: the car reports fuel as it's unlocked and again on arrival."""
    doors = [(at("08:00"), "UNLOCKED"), (at("08:09"), "SECURED")]
    odo = [(at("08:00"), 1000.0), (at("08:09"), 1008.0)]
    fuel = [FuelReading(at("08:00"), 40.0, 77.0), FuelReading(at("08:08"), 39.4, 76.0)]
    [t] = detect_trips(Series(odo), Series(doors), [], fuel, at("12:00"))
    assert (t.fuel_start_l, t.fuel_end_l) == (40.0, 39.4)
    assert round(t.fuel_used_l, 1) == 0.6


def test_trip_without_door_data_uses_quiet_gaps():
    odo = [
        (at("08:00"), 100.0),
        (at("08:05"), 104.0),
        (at("08:20"), 112.0),
        (at("17:00"), 112.0),
        (at("17:30"), 120.0),
    ]
    trips = detect_trips(Series(odo), Series([]), [], [], at("19:00"))
    assert [t.distance_km for t in trips] == [12.0, 8.0]


def test_pair_positions_joins_lat_and_lon():
    t = at("10:00")
    got = pair_positions([(t, 51.5), (at("11:00"), 52.0)], [(t + timedelta(seconds=2), -1.0)])
    assert got == [Position(t, 51.5, -1.0)]


def test_fuel_readings_fill_litres_from_percent():
    t1, t2, t3, t4 = at("09:00"), at("10:00"), at("11:00"), at("12:00")
    r = fuel_readings(
        [(t1, 30.0), (t2, 25.0), (t3, 20.0)],
        [(t1, 60.0), (t2, 50.0), (t3, 40.0), (t4, 90.0)],
        None,
    )
    assert tank_capacity(r) == 50.0
    assert (r[-1].litres, r[-1].litres_from_percent) == (45.0, True)


def test_detect_fillup_matches_station_and_merges_top_ups():
    readings = [
        FuelReading(at("08:00"), 10.0, 20.0),
        FuelReading(at("09:00"), 9.0, 18.0),
        FuelReading(at("10:10"), 40.0, 80.0),
        FuelReading(at("10:20"), 45.0, 90.0),  # topped off: same fill-up
        FuelReading(at("15:00"), 44.0, 88.0),
    ]
    positions = [Position(at("09:30"), *SHOP), Position(at("10:05"), *STATION)]

    def stations(lat, lon):
        if (lat, lon) == STATION:
            return [{"node_id": "n1", "trading_name": "Fake Fuel", "distance_m": 20.0}]
        return []

    odo = Series([(at("08:00"), 5000.0), (at("10:00"), 5030.0)])
    (fill,) = detect_fillups(readings, positions, odo, stations)
    assert fill.litres_added == 36.0
    assert fill.station["node_id"] == "n1"
    assert fill.time == at("10:05")
    assert fill.odometer == 5030.0


def test_small_wobbles_are_not_fillups():
    readings = [FuelReading(at("08:00"), 20.0, 40.0), FuelReading(at("09:00"), 22.0, 44.0)]
    assert detect_fillups(readings, [], Series([])) == []


def _fill(station=None, litres=40.0):
    r = FuelReading(at("10:00"), None, None)
    return FillUp(time=at("10:00"), before=r, after=r, litres_added=litres, station=station)


def test_price_prefers_station_then_uk_average():
    uk = lambda kind, on: (172.78, date(2026, 11, 9))  # noqa: E731
    st = {"node_id": "n1", "distance_m": 10}

    p = price_fillup(_fill(st), "E10", "petrol", lambda n, g, t: (159.9, at("06:00")), uk)
    assert (p["price_source"], p["price_ppl"], p["cost_gbp"]) == ("station", 159.9, 63.96)

    # No station match, or a nonsense station price: fall back to the UK average.
    p = price_fillup(_fill(), "E10", "petrol", lambda *a: None, uk)
    assert (p["price_source"], p["cost_gbp"]) == ("uk_average", 69.11)
    p = price_fillup(_fill(st), "E10", "petrol", lambda n, g, t: (299.9, at("06:00")), uk)
    assert p["price_source"] == "uk_average"

    p = price_fillup(_fill(), "E10", "petrol", lambda *a: None, lambda *a: None)
    assert p["price_source"] is None and p["cost_gbp"] is None


def test_fuel_kind_from_basic_data():
    assert fuel_kind({"propulsionType": "BE"}) == "petrol"
    assert fuel_kind({"propulsionType": "DI"}) == "diesel"
    assert fuel_kind(None) == "petrol"
