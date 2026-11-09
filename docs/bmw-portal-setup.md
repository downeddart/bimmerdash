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
2. Select **all** data points. Streaming may deliver incomplete data otherwise.
   There are around 245; to tick them all at once, open the browser console (F12) and run:

   ```js
   document.querySelectorAll('input[type="checkbox"]:not(:checked)').forEach(c => c.click());
   ```

   Expand collapsed sections first, and untick anything unrelated it may have caught.
3. Save.

## Notes

- BMW allows **one streaming connection per account**. Stop any other CarData client
  (Home Assistant integrations, MQTT explorers) while bimmerdash runs.
- The stream only sends data when the car has something new. Locking or unlocking the car
  from the app is a quick way to trigger a message.
- Updates arrive roughly every 20 minutes while driving, not continuously.
