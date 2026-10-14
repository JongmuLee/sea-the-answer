-- migrate:up
-- External reference snapshot only. Never fills or overwrites source MRV fields.
SET LOCAL lock_timeout = '5s';
CREATE TABLE maritime_data.capacity_candidates (
    vessel_id text PRIMARY KEY REFERENCES maritime_data.vessels(vessel_id),
    imo_number text NOT NULL CHECK (imo_number ~ '^[0-9]{7}$'),
    candidate_dwt_t numeric,
    capacity_status text NOT NULL CHECK (capacity_status IN (
        'UNAVAILABLE','WIKIDATA_ONLY','MARINE_VESSELS_ONLY',
        'AGREE_WITHIN_5_PERCENT','CONFLICT_OVER_5_PERCENT')),
    source_file_sha256 text NOT NULL CHECK (source_file_sha256 ~ '^[0-9a-f]{64}$'),
    source_filename text NOT NULL,
    source_row integer NOT NULL CHECK (source_row >= 2),
    source_json jsonb NOT NULL CHECK (jsonb_typeof(source_json) = 'object'),
    loaded_at timestamptz NOT NULL DEFAULT now(),
    CHECK (vessel_id = 'REAL:IMO:' || imo_number),
    CHECK (candidate_dwt_t IS NULL OR (candidate_dwt_t > 0 AND candidate_dwt_t < 10000000)),
    CHECK ((capacity_status = 'UNAVAILABLE') = (candidate_dwt_t IS NULL))
);
COMMENT ON TABLE maritime_data.capacity_candidates IS
  'Reference only; not statutory or year-specific verified capacity. Excluded from app JSON backup.';

-- migrate:down
SET LOCAL lock_timeout = '5s';
DO $guard$
BEGIN
    IF EXISTS (SELECT FROM maritime_data.capacity_candidates) THEN
        RAISE EXCEPTION 'Capacity references are not empty; export and review before rollback';
    END IF;
END $guard$;
DROP TABLE maritime_data.capacity_candidates;
