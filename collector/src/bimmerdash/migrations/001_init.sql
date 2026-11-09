-- Core storage. Everything is keyed by VIN so multiple cars on one account work.
CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE vehicles (
    vin                text PRIMARY KEY,
    model_name         text,
    series             text,
    body_type          text,
    colour             text,
    basic_data         jsonb,
    image              bytea,
    image_type         text,
    first_seen         timestamptz NOT NULL DEFAULT now(),
    details_fetched_at timestamptz
);

-- Every MQTT message exactly as received. Source of truth: derived tables can be rebuilt.
CREATE TABLE raw_messages (
    received_at  timestamptz NOT NULL,
    topic        text        NOT NULL,
    vin          text,
    payload      jsonb       NOT NULL,
    payload_hash bytea       NOT NULL,
    PRIMARY KEY (received_at, payload_hash)
);
SELECT create_hypertable('raw_messages', by_range('received_at', INTERVAL '7 days'));

-- One row per data point: works for any of the ~245 CarData keys without schema changes.
CREATE TABLE telemetry (
    time      timestamptz NOT NULL,
    vin       text        NOT NULL,
    key       text        NOT NULL,
    value     jsonb,
    value_num double precision,
    unit      text,
    PRIMARY KEY (vin, key, time)
);
SELECT create_hypertable('telemetry', by_range('time', INTERVAL '7 days'));
CREATE INDEX telemetry_key_time ON telemetry (key, time DESC);

-- Current value of each key, for fast "car status" reads.
CREATE TABLE latest_values (
    vin       text        NOT NULL,
    key       text        NOT NULL,
    time      timestamptz NOT NULL,
    value     jsonb,
    value_num double precision,
    unit      text,
    PRIMARY KEY (vin, key)
);

-- REST calls, to stay inside BMW's 50-per-day quota.
CREATE TABLE api_calls (
    time     timestamptz NOT NULL DEFAULT now(),
    endpoint text        NOT NULL,
    status   integer
);
CREATE INDEX api_calls_time ON api_calls (time DESC);
