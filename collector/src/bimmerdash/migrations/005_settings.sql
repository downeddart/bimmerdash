-- User settings from the web UI. scope is 'global' or a VIN.
CREATE TABLE settings (
    scope      text        NOT NULL,
    key        text        NOT NULL,
    value      jsonb       NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scope, key)
);
