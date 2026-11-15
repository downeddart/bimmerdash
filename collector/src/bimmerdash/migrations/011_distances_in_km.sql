-- A REST poll can report a distance (odometer, range) in miles where the stream uses km,
-- which broke anything that compares readings of one key. Ingest now converts miles to
-- km; this converts rows stored before that. raw_messages keeps what BMW sent.
UPDATE telemetry
SET value_num = value_num * 1.609344,
    value = to_jsonb(round((value_num * 1.609344)::numeric, 3)),
    unit = 'km'
WHERE lower(unit) IN ('mi', 'mile', 'miles') AND value_num IS NOT NULL;

UPDATE latest_values
SET value_num = value_num * 1.609344,
    value = to_jsonb(round((value_num * 1.609344)::numeric, 3)),
    unit = 'km'
WHERE lower(unit) IN ('mi', 'mile', 'miles') AND value_num IS NOT NULL;
