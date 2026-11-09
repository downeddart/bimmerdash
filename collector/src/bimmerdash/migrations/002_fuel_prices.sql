-- UK Fuel Finder (statutory open data): every forecourt and its price history.

CREATE TABLE forecourts (
    node_id        text PRIMARY KEY,
    trading_name   text,
    brand_name     text,
    postcode       text,
    latitude       double precision,
    longitude      double precision,
    is_motorway    boolean,
    is_supermarket boolean,
    closed         boolean NOT NULL DEFAULT false,
    fuel_types     text[],
    raw            jsonb,
    updated_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX forecourts_lat_lon ON forecourts (latitude, longitude);

-- One row per price change (pence per litre, as published).
CREATE TABLE fuel_prices (
    node_id      text             NOT NULL,
    fuel_type    text             NOT NULL,
    effective_at timestamptz      NOT NULL,
    price        double precision NOT NULL,
    observed_at  timestamptz      NOT NULL DEFAULT now(),
    PRIMARY KEY (node_id, fuel_type, effective_at)
);
SELECT create_hypertable('fuel_prices', by_range('effective_at', INTERVAL '30 days'));

-- Sync bookkeeping (single row).
CREATE TABLE fuel_finder_state (
    id               integer PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    forecourts_at    timestamptz,
    full_prices_at   timestamptz,
    incremental_at   timestamptz
);
INSERT INTO fuel_finder_state DEFAULT VALUES;
