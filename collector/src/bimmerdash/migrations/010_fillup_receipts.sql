-- What you actually paid at the pump, entered in the web UI. Kept apart from fillups,
-- which the collector rebuilds from scratch, so a receipt survives every rebuild. The web
-- UI matches each receipt to the nearest detected fill-up when it reads them.
CREATE TABLE fillup_receipts (
    vin         text             NOT NULL,
    fillup_time timestamptz      NOT NULL,  -- the fill-up's time when the receipt was entered
    cost_gbp    double precision NOT NULL CHECK (cost_gbp > 0 AND cost_gbp <= 500),
    litres      double precision CHECK (litres IS NULL OR (litres > 0 AND litres <= 150)),
    updated_at  timestamptz      NOT NULL DEFAULT now(),
    PRIMARY KEY (vin, fillup_time)
);

GRANT INSERT, UPDATE, DELETE ON fillup_receipts TO bimmerdash_web;
