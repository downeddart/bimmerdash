"""CarData key names. Where BMW has alternatives, the first one present wins."""

ODOMETER = ["vehicle.vehicle.travelledDistance"]
FUEL_PERCENT = ["vehicle.drivetrain.fuelSystem.level"]
FUEL_LITRES = ["vehicle.drivetrain.fuelSystem.remainingFuel"]
RANGE = [
    "vehicle.drivetrain.lastRemainingRange",
    "vehicle.drivetrain.totalRemainingRange",
    "vehicle.cabin.infotainment.navigation.remainingRange",
]
LATITUDE = ["vehicle.cabin.infotainment.navigation.currentLocation.latitude"]
LONGITUDE = ["vehicle.cabin.infotainment.navigation.currentLocation.longitude"]
HEADING = ["vehicle.cabin.infotainment.navigation.currentLocation.heading"]
DOOR_LOCK = ["vehicle.cabin.door.lock.status"]
DOORS = ["vehicle.cabin.door.status"]
TRUNK_OPEN = ["vehicle.body.trunk.isOpen"]
WINDOWS = [
    "vehicle.cabin.window.row1.driver.status",
    "vehicle.cabin.window.row1.passenger.status",
    "vehicle.cabin.window.row2.driver.status",
    "vehicle.cabin.window.row2.passenger.status",
]
TYRES = {
    "front_left": "vehicle.chassis.axle.row1.wheel.left.tire.pressure",
    "front_right": "vehicle.chassis.axle.row1.wheel.right.tire.pressure",
    "rear_left": "vehicle.chassis.axle.row2.wheel.left.tire.pressure",
    "rear_right": "vehicle.chassis.axle.row2.wheel.right.tire.pressure",
}
SERVICE_DISTANCE = ["vehicle.status.serviceDistance.next"]
CHECK_CONTROL = ["vehicle.status.checkControlMessages"]
IS_MOVING = ["vehicle.isMoving"]
BATTERY_12V = ["vehicle.electricalSystem.battery.voltage"]
