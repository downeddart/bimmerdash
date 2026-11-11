-- Small pieces of collector state, e.g. the CarData container the REST poller reads.
CREATE TABLE collector_state (
    key        text PRIMARY KEY,
    value      jsonb       NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Alerts raised by the collector. One open row per (vin, kind, ref): an alert is sent
-- once when raised, and can be raised again only after it has cleared.
CREATE TABLE alerts (
    id         serial PRIMARY KEY,
    vin        text        NOT NULL,
    kind       text        NOT NULL,
    ref        text        NOT NULL DEFAULT '',
    title      text        NOT NULL,
    message    text        NOT NULL,
    raised_at  timestamptz NOT NULL DEFAULT now(),
    sent_at    timestamptz,
    send_error text,
    cleared_at timestamptz
);
CREATE UNIQUE INDEX alerts_open ON alerts (vin, kind, ref) WHERE cleared_at IS NULL;
CREATE INDEX alerts_recent ON alerts (vin, raised_at DESC);

-- Share of each trip driven in ECO PRO / ECO PRO+ (the car reports it at the end).
ALTER TABLE trips
    ADD COLUMN eco_pro_pct      double precision,
    ADD COLUMN eco_pro_plus_pct double precision;
