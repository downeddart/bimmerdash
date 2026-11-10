-- Places you name in the web UI ("Home", "Work"). Trips that start or end within
-- radius_m of one are labelled with it.
CREATE TABLE places (
    id         serial PRIMARY KEY,
    name       text             NOT NULL,
    lat        double precision NOT NULL,
    lon        double precision NOT NULL,
    radius_m   double precision NOT NULL DEFAULT 150,
    created_at timestamptz      NOT NULL DEFAULT now()
);
