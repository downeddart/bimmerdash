"""Alert rules, the REST poller's parsing, and ECO PRO per trip. Pure functions, no DB."""

from datetime import UTC, date, datetime, timedelta

from bimmerdash import alerts
from bimmerdash.derive import Series, Trip, attach_drive_modes
from bimmerdash.telematic import CHECK_CONTROL, DESCRIPTORS, parse_value, signature, to_record

VIN = "WBA0000000TEST000"  # fake
NOW = datetime(2026, 11, 10, 20, 0, tzinfo=UTC)


def lv(**values):
    """Latest values from short names, all reported 30 min ago."""
    names = {
        "doors": alerts.DOORS,
        "driver_window": "vehicle.cabin.window.row1.driver.status",
        "boot": "vehicle.body.trunk.isOpen",
        "fuel": alerts.FUEL_PERCENT,
        "litres": alerts.FUEL_LITRES,
        "range": alerts.RANGE,
        "cbs": alerts.CBS,
        "mot": alerts.MOT_DATE,
        "cc": alerts.CHECK_CONTROL,
        "fl": "vehicle.chassis.axle.row1.wheel.left.tire.pressure",
        "fl_target": "vehicle.chassis.axle.row1.wheel.left.tire.pressureTarget",
    }
    t = NOW - timedelta(minutes=30)
    return {names[k]: {"value": v, "unit": None, "time": t} for k, v in values.items()}


# --- REST poller -----------------------------------------------------------------------


def test_parse_value():
    assert parse_value("42") == 42 and isinstance(parse_value("42"), int)
    assert parse_value("4697.56") == 4697.56
    assert parse_value('[{"type": "OIL"}]') == [{"type": "OIL"}]
    assert parse_value("SECURED") == "SECURED"
    assert parse_value("[not json") == "[not json"


def test_to_record_skips_missing_and_empties_check_control():
    rec = to_record(
        VIN,
        {
            "vehicle.drivetrain.fuelSystem.level": {"value": "43", "unit": "%", "timestamp": None},
            "vehicle.status.conditionBasedServices": {"value": None},
            CHECK_CONTROL: {"value": None, "unit": None, "timestamp": None},
        },
        NOW,
    )
    data = rec["payload"]["data"]
    assert rec["payload"]["vin"] == VIN and rec["topic"].startswith("rest/")
    assert data["vehicle.drivetrain.fuelSystem.level"]["value"] == 43
    assert data["vehicle.drivetrain.fuelSystem.level"]["timestamp"] == NOW.isoformat()
    assert "vehicle.status.conditionBasedServices" not in data  # not reported: skipped
    assert data[CHECK_CONTROL]["value"] == []  # null here means "no warnings"


def test_signature_ignores_order():
    assert signature(DESCRIPTORS) == signature(list(reversed(DESCRIPTORS)))
    assert signature(DESCRIPTORS) != signature(DESCRIPTORS[1:])


# --- alert rules -----------------------------------------------------------------------


def test_left_open_only_once_quiet():
    values = lv(doors="UNLOCKED", driver_window="OPEN", boot=False)
    quiet = NOW - timedelta(minutes=30)
    [a] = alerts.left_open(values, NOW, 10, quiet)
    assert a.title == "Car left unlocked" and "driver's window open" in a.message
    assert alerts.left_open(values, NOW, 10, NOW - timedelta(minutes=5)) == []
    assert alerts.left_open(lv(doors="SECURED", driver_window="CLOSED"), NOW, 10, quiet) == []
    [boot] = alerts.left_open(lv(doors="LOCKED", boot=True), NOW, 10, quiet)
    assert boot.title == "Car left open" and "boot open" in boot.message


def test_low_fuel_hysteresis_and_station():
    cheap = {"name": "Cheap Fuel", "price": 139.9, "km": 3.2}
    [a] = alerts.low_fuel(lv(fuel=18, litres=9, range=150), 20, False, cheap)
    assert "18% left (9 L)" in a.message and "Cheap Fuel, 139.9p" in a.message
    assert "93 mi" in a.message  # 150 km
    # 23%: not raised fresh, but an open alert stays until 25%.
    assert alerts.low_fuel(lv(fuel=23), 20, False) == []
    assert len(alerts.low_fuel(lv(fuel=23), 20, True)) == 1
    assert alerts.low_fuel(lv(fuel=26), 20, True) == []


def test_num_rejects_non_finite():
    assert alerts._num(lv(fuel="inf"), alerts.FUEL_PERCENT) is None
    assert alerts._num(lv(fuel="nan"), alerts.FUEL_PERCENT) is None
    assert alerts._num(lv(fuel="-"), alerts.FUEL_PERCENT) is None
    assert alerts._num(lv(fuel="12.5"), alerts.FUEL_PERCENT) == 12.5


def test_tyres_low_against_target():
    [a] = alerts.tyres(lv(fl=207, fl_target=234), 10, set())
    assert a.ref == "front left" and "30 psi" in a.message and "12% below" in a.message
    assert alerts.tyres(lv(fl=225, fl_target=234), 10, set()) == []  # 4% below: fine
    # Once raised, it stays until within 5% of target.
    assert len(alerts.tyres(lv(fl=218, fl_target=234), 10, {"front left"})) == 1
    assert alerts.tyres(lv(fl=230, fl_target=234), 10, {"front left"}) == []


def test_service_due_and_mot():
    cbs = [
        {"type": "OIL", "title": "Engine oil", "status": "OK", "date": "2026-12-01"},
        {"type": "BRAKE_FLUID", "title": "Brake fluid", "status": "OK", "date": "2028-01"},
        {"type": "VEHICLE_CHECK", "title": "Vehicle check", "status": "OVERDUE", "date": None},
    ]
    found = alerts.service(lv(cbs=cbs, mot="01.09.2027 00:00:00 UTC"), date(2026, 11, 10), 30)
    titles = sorted(a.title for a in found)
    assert titles == ["Engine oil due soon", "Vehicle check overdue"]
    [mot] = alerts.service(lv(mot="2026-11-20"), date(2026, 11, 10), 30)
    assert mot.ref == "mot" and "20 Nov 2026" in mot.message


def test_warnings_one_per_message():
    found = alerts.warnings(lv(cc=[{"id": 229, "title": "Washer fluid low"}, "Check engine"]))
    assert [a.message for a in found] == ["Washer fluid low", "Check engine"]
    assert found[0].ref == "229"
    assert alerts.warnings(lv(cc=[])) == []


# --- ECO PRO per trip ------------------------------------------------------------------


def test_attach_drive_modes_takes_the_summary_sent_at_the_end():
    t0 = NOW - timedelta(hours=2)
    trip = Trip(t0, t0 + timedelta(minutes=20), None, None, 10, None, None)
    eco = Series([(t0 + timedelta(minutes=22), 35.0), (t0 + timedelta(hours=1), 90.0)])
    plus = Series([])
    attach_drive_modes([trip], eco, plus)
    assert trip.eco_pro_pct == 35.0 and trip.eco_pro_plus_pct is None
