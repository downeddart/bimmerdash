# BMW portal setup

bimmerdash uses **BMW CarData**, BMW's official customer data access. It is free for
your own car. CarData currently appears to be offered in EU/UK markets only.

CarData is only on the **BMW website**, not in the MyBMW phone app.

## 1. Find CarData

1. Sign in at your country's BMW site → **My BMW**, using your BMW ID.
2. Your car must be linked to your BMW ID with you as **primary user**.
3. Open the vehicle overview and select the **CarData** tile. In some regions it is under
   *Personal Data → My Vehicles → CarData*.

## 2. Create a CarData client

1. Scroll to **Technical access to BMW CarData** → **Create CarData Client**.
2. Copy the **Client ID** into `BIMMERDASH_CLIENT_ID` in your `.env`.
3. Enable **both** switches: *Request access to CarData API* and *CarData Stream*.
   If you get an error, close the tab, reload and try again.

## 3. Create a stream

1. Scroll to **CarData Streaming** and create a stream.
2. Select **all** data points (around 250). Streaming only sends what is ticked here.
   Click **Load more** until the button disappears, then tick each box by hand. The portal
   reloads the list after every tick, dropping back to the first ~75 items, so you will need
   to click **Load more** again to reach the rest. Browser-console "tick all" scripts do not
   work against this behaviour.
3. Save.
4. Check what actually arrives: after the next lock/unlock or drive, run
   `docker compose run --rm collector keys --redact`. A full selection gives far more than
   a handful of keys; fuel level (`vehicle.drivetrain.fuelSystem.level`) and position
   (`vehicle.cabin.infotainment.navigation.currentLocation.latitude`) are the important ones.

## Notes

- BMW allows **one streaming connection per account**. Stop any other CarData client
  (Home Assistant integrations, MQTT explorers) while bimmerdash runs.
- The stream only sends data when the car has something new. Locking or unlocking the car
  from the app is a quick way to trigger a message.
- Updates arrive roughly every 20 minutes while driving, not continuously.
