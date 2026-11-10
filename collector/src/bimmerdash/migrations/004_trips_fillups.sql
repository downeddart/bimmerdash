-- Derived from telemetry by bimmerdash.derive and rebuilt in full on each run,
-- so nothing here is the source of truth.

CREATE TABLE trips (
    vin            text        NOT NULL,
    start_time     timestamptz NOT NULL,
    end_time       timestamptz NOT NULL,
    start_odometer double precision,
    end_odometer   double precision,
    distance_km    double precision NOT NULL,
    start_lat      double precision,
    start_lon      double precision,
    end_lat        double precision,
    end_lon        double precision,
    fuel_start_l   double precision,
    fuel_end_l     double precision,
    fuel_used_l    double precision,
    route          jsonb,               -- [[iso time, lat, lon], ...]: sparse, not a GPS track
    PRIMARY KEY (vin, start_time)
);

CREATE TABLE fillups (
    vin              text        NOT NULL,
    time             timestamptz NOT NULL,
    odometer         double precision,
    litres_before    double precision,
    litres_after     double precision,
    percent_before   double precision,
    percent_after    double precision,
    litres_added     double precision,
    litres_estimated boolean NOT NULL DEFAULT false,  -- worked out from % and tank size
    lat              double precision,
    lon              double precision,
    node_id          text,               -- matched Fuel Finder forecourt, if any
    station          jsonb,
    distance_m       double precision,
    fuel_grade       text,               -- E10, E5, B7_STANDARD, ...
    fuel_kind        text,               -- petrol / diesel (UK average series)
    price_ppl        double precision,   -- pence per litre
    price_source     text CHECK (price_source IN ('receipt', 'station', 'uk_average')),
    price_ref        text,               -- price effective time, or the average's week
    cost_gbp         double precision,
    PRIMARY KEY (vin, time)
);

ALTER TABLE vehicles ADD COLUMN derived_at timestamptz;
ALTER TABLE vehicles ADD COLUMN tank_litres double precision;
ALTER TABLE vehicles ADD COLUMN fuel_grade text;
ALTER TABLE vehicles ADD COLUMN fuel_kind text;
