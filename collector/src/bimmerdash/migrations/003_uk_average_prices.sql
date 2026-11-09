-- DESNZ "Weekly road fuel prices": UK average pump prices since June 2003.
-- Used to estimate the cost of fill-ups for which no station price is known.
CREATE TABLE uk_average_fuel_prices (
    week_start date             NOT NULL,
    fuel       text             NOT NULL,  -- 'petrol' (ULSP) or 'diesel' (ULSD)
    price      double precision NOT NULL,  -- pence per litre, including duty and VAT
    PRIMARY KEY (fuel, week_start)
);

ALTER TABLE fuel_finder_state ADD COLUMN uk_averages_at timestamptz;
