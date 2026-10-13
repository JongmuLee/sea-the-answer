-- migrate:up
-- Reuse the app database and ships; retain source observations and provenance.
-- dbmate owns the transaction. No downloads or dataset writes occur here.
SET LOCAL lock_timeout = '5s';
DO $migration$
DECLARE expected jsonb := '{"source_files":[["source_id","text",false],["dataset_id","text",false],["filename","text",false],["sha256","text",false],["source_url","text",true],["data_origin","text",false],["granularity","text",false],["selection","text",false],["row_count","int4",false],["header_json","jsonb",false]],"vessels":[["vessel_id","text",false],["data_origin","text",false],["imo_number","text",true],["source_vessel_id","text",false]],"annual_reports":[["record_id","text",false],["source_id","text",false],["source_sheet","text",false],["source_row","int4",false],["vessel_id","text",false],["vessel_name","text",true],["ship_type","text",true],["reporting_year","int4",false],["report_type","text",false],["period_label","text",false],["period_start","date",true],["period_end","date",true],["fuel_t","numeric",true],["co2_t","numeric",true],["sea_hours","numeric",true],["fuel_kg_per_nm","numeric",true],["distance_nm_estimate","numeric",true],["dwt_t","numeric",true],["fuel_type","text",true],["quality_status","text",false],["aggregate_eligible","bool",false],["provenance_json","jsonb",false]],"synthetic_noon":[["record_id","text",false],["source_id","text",false],["source_row","int4",false],["vessel_id","text",false],["voyage_id","text",false],["report_date","date",false],["period_start_utc","timestamptz",false],["observed_at_utc","timestamptz",false],["vessel_name","text",false],["ship_type","text",false],["dwt_t","numeric",false],["operating_status","text",false],["distance_nm","numeric",false],["fuel_t","numeric",false],["fuel_type","text",false],["speed_kn","numeric",false],["engine_hours","numeric",false],["quality_status","text",false],["provenance_json","jsonb",false]],"synthetic_voyages":[["voyage_id","text",false],["vessel_id","text",false],["departure_port_label","text",true],["arrival_port_label","text",true],["first_report_date","date",false],["last_report_date","date",false],["mapping_status","text",false]],"quality_issues":[["issue_id","int4",false],["record_id","text",true],["source_id","text",false],["source_row","int4",false],["severity","text",false],["field","text",false],["code","text",false],["detail","text",false]]}'::jsonb;
        expected_fks jsonb := '{"source_files":[],"vessels":[],"synthetic_voyages":[["vessel_id","vessels","vessel_id"]],"annual_reports":[["source_id","source_files","source_id"],["vessel_id","vessels","vessel_id"]],"synthetic_noon":[["source_id","source_files","source_id"],["vessel_id","vessels","vessel_id"],["voyage_id","synthetic_voyages","voyage_id"]],"quality_issues":[["source_id","source_files","source_id"]]}'::jsonb;
        entry record;
        actual jsonb;
        foreign_key jsonb;
        table_oid oid;
        target_oid oid;
        contract_oid oid;
        expected_checks jsonb;
BEGIN
  IF to_regclass('public.ships') IS NULL THEN
    RAISE EXCEPTION 'Apply the app init migration before maritime data';
  END IF;
  IF EXISTS (SELECT FROM pg_namespace WHERE nspname='role4')
     AND EXISTS (SELECT FROM pg_namespace WHERE nspname='maritime_data') THEN
    RAISE EXCEPTION 'Both role4 and maritime_data exist; no data was merged or overwritten';
  END IF;
  IF EXISTS (SELECT FROM pg_namespace WHERE nspname='role4') THEN
    ALTER SCHEMA role4 RENAME TO maritime_data;
  END IF;
  IF EXISTS (SELECT FROM pg_namespace WHERE nspname='maritime_data') THEN
    FOR entry IN SELECT * FROM jsonb_each(expected) LOOP
      IF NOT EXISTS (SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                     WHERE n.nspname='maritime_data' AND c.relname=entry.key AND c.relkind='r') THEN
        RAISE EXCEPTION 'Unsupported existing maritime table: %', entry.key;
      END IF;
      SELECT jsonb_agg(jsonb_build_array(column_name,udt_name,is_nullable='YES') ORDER BY ordinal_position)
        INTO actual FROM information_schema.columns
        WHERE table_schema='maritime_data' AND table_name=entry.key;
      IF actual IS DISTINCT FROM entry.value THEN
        RAISE EXCEPTION 'Unsupported existing maritime columns: %', entry.key;
      END IF;
      -- Only the known four-column legacy vessels shape is adopted. An existing
      -- ship_id or other extension needs its own reviewed migration, not a guess.
      table_oid := to_regclass(format('maritime_data.%I', entry.key));
      IF NOT EXISTS (
        SELECT FROM pg_constraint c
        WHERE c.conrelid=table_oid AND c.contype='p'
          AND c.conkey=ARRAY[(SELECT attnum FROM pg_attribute
                             WHERE attrelid=table_oid AND attname=entry.value->0->>0)]::smallint[]
          AND c.convalidated AND NOT c.condeferrable AND NOT c.condeferred
          AND coalesce((to_jsonb(c)->>'conenforced')::boolean, true)
      ) THEN
        RAISE EXCEPTION 'Unsupported existing maritime primary key: %', entry.key;
      END IF;
      IF (SELECT count(*) FROM pg_constraint WHERE conrelid=table_oid AND contype='f')
         <> jsonb_array_length(expected_fks->entry.key) THEN
        RAISE EXCEPTION 'Unsupported existing maritime foreign keys: %', entry.key;
      END IF;
      FOR foreign_key IN SELECT value FROM jsonb_array_elements(expected_fks->entry.key) LOOP
        target_oid := to_regclass(format('maritime_data.%I', foreign_key->>1));
        IF NOT EXISTS (
          SELECT FROM pg_constraint c
          WHERE c.conrelid=table_oid AND c.contype='f' AND c.confrelid=target_oid
            AND c.conkey=ARRAY[(SELECT attnum FROM pg_attribute
                               WHERE attrelid=table_oid AND attname=foreign_key->>0)]::smallint[]
            AND c.confkey=ARRAY[(SELECT attnum FROM pg_attribute
                                WHERE attrelid=target_oid AND attname=foreign_key->>2)]::smallint[]
            AND c.confdeltype='r' AND c.confupdtype='a' AND c.confmatchtype='s'
            AND c.convalidated AND NOT c.condeferrable AND NOT c.condeferred
            AND coalesce((to_jsonb(c)->>'conenforced')::boolean, true)
        ) THEN
          RAISE EXCEPTION 'Unsupported existing maritime foreign key: %.%', entry.key, foreign_key->>0;
        END IF;
      END LOOP;
      -- Parse the frozen legacy CHECK contract on this same PostgreSQL server.
      -- LIKE copies column types/NOT NULL, but does not copy CHECK constraints.
      -- Comparing canonical expressions avoids relying on names or whitespace.
      contract_oid := NULL;
      IF entry.key='annual_reports' THEN
        CREATE TEMP TABLE _haedap_maritime_contract_annual (
          LIKE maritime_data.annual_reports,
          CHECK (quality_status IN ('VALID','REVIEW','REJECT')),
          CHECK (vessel_id LIKE 'REAL:IMO:%'),
          CHECK (report_type IN ('FULL','PARTIAL','ARCHIVE_ANNUAL')),
          CHECK (NOT aggregate_eligible OR (quality_status = 'VALID' AND report_type <> 'PARTIAL')),
          CHECK (period_end >= period_start)
        ) ON COMMIT DROP;
        contract_oid := 'pg_temp._haedap_maritime_contract_annual'::regclass;
      ELSIF entry.key='synthetic_noon' THEN
        CREATE TEMP TABLE _haedap_maritime_contract_noon (
          LIKE maritime_data.synthetic_noon,
          CHECK (quality_status IN ('VALID','REVIEW','REJECT')),
          CHECK (vessel_id LIKE 'SYN:%'),
          CHECK (dwt_t > 0 AND distance_nm >= 0 AND fuel_t >= 0 AND speed_kn >= 0),
          CHECK (engine_hours BETWEEN 0 AND 24),
          CHECK (observed_at_utc > period_start_utc AND observed_at_utc <= period_start_utc + interval '24 hours')
        ) ON COMMIT DROP;
        contract_oid := 'pg_temp._haedap_maritime_contract_noon'::regclass;
      END IF;
      SELECT coalesce(jsonb_agg(pg_get_expr(conbin,conrelid) ORDER BY pg_get_expr(conbin,conrelid)), '[]'::jsonb)
        INTO expected_checks FROM pg_constraint WHERE conrelid=contract_oid AND contype='c';
      SELECT coalesce(jsonb_agg(pg_get_expr(conbin,conrelid) ORDER BY pg_get_expr(conbin,conrelid)), '[]'::jsonb)
        INTO actual FROM pg_constraint WHERE conrelid=table_oid AND contype='c';
      IF actual IS DISTINCT FROM expected_checks OR EXISTS (
        SELECT FROM pg_constraint c WHERE c.conrelid=table_oid AND c.contype='c'
          AND (NOT c.convalidated OR c.connoinherit
               OR NOT coalesce((to_jsonb(c)->>'conenforced')::boolean, true))
      ) THEN
        RAISE EXCEPTION 'Unsupported existing maritime checks: %', entry.key;
      END IF;
    END LOOP;
  END IF;
END $migration$;
CREATE SCHEMA IF NOT EXISTS maritime_data;

CREATE TABLE IF NOT EXISTS maritime_data.source_files (
  source_id text PRIMARY KEY,
  dataset_id text NOT NULL,
  filename text NOT NULL,
  sha256 text NOT NULL,
  source_url text,
  data_origin text NOT NULL,
  granularity text NOT NULL,
  selection text NOT NULL,
  row_count integer NOT NULL,
  header_json jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS maritime_data.vessels (
  vessel_id text PRIMARY KEY,
  data_origin text NOT NULL,
  imo_number text,
  source_vessel_id text NOT NULL
);

CREATE TABLE IF NOT EXISTS maritime_data.synthetic_voyages (
  voyage_id text PRIMARY KEY,
  vessel_id text NOT NULL REFERENCES maritime_data.vessels(vessel_id) ON DELETE RESTRICT,
  departure_port_label text,
  arrival_port_label text,
  first_report_date date NOT NULL,
  last_report_date date NOT NULL,
  mapping_status text NOT NULL
);

CREATE TABLE IF NOT EXISTS maritime_data.annual_reports (
  record_id text PRIMARY KEY,
  source_id text NOT NULL REFERENCES maritime_data.source_files(source_id) ON DELETE RESTRICT,
  source_sheet text NOT NULL,
  source_row integer NOT NULL,
  vessel_id text NOT NULL REFERENCES maritime_data.vessels(vessel_id) ON DELETE RESTRICT,
  vessel_name text,
  ship_type text,
  reporting_year integer NOT NULL,
  report_type text NOT NULL,
  period_label text NOT NULL,
  period_start date,
  period_end date,
  fuel_t numeric,
  co2_t numeric,
  sea_hours numeric,
  fuel_kg_per_nm numeric,
  distance_nm_estimate numeric,
  dwt_t numeric,
  fuel_type text,
  quality_status text NOT NULL,
  aggregate_eligible boolean NOT NULL,
  provenance_json jsonb NOT NULL,
  CHECK (quality_status IN ('VALID','REVIEW','REJECT')),
  CHECK (vessel_id LIKE 'REAL:IMO:%'),
  CHECK (report_type IN ('FULL','PARTIAL','ARCHIVE_ANNUAL')),
  CHECK (NOT aggregate_eligible OR (quality_status = 'VALID' AND report_type <> 'PARTIAL')),
  CHECK (period_end >= period_start)
);

CREATE TABLE IF NOT EXISTS maritime_data.synthetic_noon (
  record_id text PRIMARY KEY,
  source_id text NOT NULL REFERENCES maritime_data.source_files(source_id) ON DELETE RESTRICT,
  source_row integer NOT NULL,
  vessel_id text NOT NULL REFERENCES maritime_data.vessels(vessel_id) ON DELETE RESTRICT,
  voyage_id text NOT NULL REFERENCES maritime_data.synthetic_voyages(voyage_id) ON DELETE RESTRICT,
  report_date date NOT NULL,
  period_start_utc timestamptz NOT NULL,
  observed_at_utc timestamptz NOT NULL,
  vessel_name text NOT NULL,
  ship_type text NOT NULL,
  dwt_t numeric NOT NULL,
  operating_status text NOT NULL,
  distance_nm numeric NOT NULL,
  fuel_t numeric NOT NULL,
  fuel_type text NOT NULL,
  speed_kn numeric NOT NULL,
  engine_hours numeric NOT NULL,
  quality_status text NOT NULL,
  provenance_json jsonb NOT NULL,
  CHECK (quality_status IN ('VALID','REVIEW','REJECT')),
  CHECK (vessel_id LIKE 'SYN:%'),
  CHECK (dwt_t > 0 AND distance_nm >= 0 AND fuel_t >= 0 AND speed_kn >= 0),
  CHECK (engine_hours BETWEEN 0 AND 24),
  CHECK (observed_at_utc > period_start_utc AND observed_at_utc <= period_start_utc + interval '24 hours')
);

CREATE TABLE IF NOT EXISTS maritime_data.quality_issues (
  issue_id integer PRIMARY KEY,
  record_id text,
  source_id text NOT NULL REFERENCES maritime_data.source_files(source_id) ON DELETE RESTRICT,
  source_row integer NOT NULL,
  severity text NOT NULL,
  field text NOT NULL,
  code text NOT NULL,
  detail text NOT NULL
);

CREATE INDEX IF NOT EXISTS annual_vessel_year ON maritime_data.annual_reports(vessel_id, reporting_year);

CREATE INDEX IF NOT EXISTS noon_vessel_date ON maritime_data.synthetic_noon(vessel_id, report_date);

CREATE INDEX IF NOT EXISTS noon_voyage ON maritime_data.synthetic_noon(voyage_id);

CREATE OR REPLACE VIEW maritime_data.real_annual_query AS SELECT * FROM maritime_data.annual_reports WHERE aggregate_eligible;

CREATE OR REPLACE VIEW maritime_data.development_noon_query AS SELECT * FROM maritime_data.synthetic_noon WHERE quality_status='VALID';

-- An explicit optional association, not an automatic IMO/name join or UI import.
ALTER TABLE maritime_data.vessels ADD COLUMN IF NOT EXISTS ship_id text;
DO $links$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_constraint WHERE conrelid='maritime_data.vessels'::regclass
                 AND conname='vessels_ship_id_fkey') THEN
    ALTER TABLE maritime_data.vessels ADD CONSTRAINT vessels_ship_id_fkey
      FOREIGN KEY (ship_id) REFERENCES public.ships(id) ON DELETE SET NULL;
  END IF;
END $links$;
CREATE UNIQUE INDEX IF NOT EXISTS vessels_one_app_ship
  ON maritime_data.vessels(ship_id) WHERE ship_id IS NOT NULL;

-- migrate:down
-- Refuse destructive rollback of an imported/adopted dataset.
DO $rollback$
BEGIN
  IF EXISTS (SELECT FROM maritime_data.source_files)
     OR EXISTS (SELECT FROM maritime_data.vessels)
     OR EXISTS (SELECT FROM maritime_data.synthetic_voyages)
     OR EXISTS (SELECT FROM maritime_data.annual_reports)
     OR EXISTS (SELECT FROM maritime_data.synthetic_noon)
     OR EXISTS (SELECT FROM maritime_data.quality_issues) THEN
    RAISE EXCEPTION 'Maritime data is not empty; export and review before rollback';
  END IF;
END $rollback$;
DROP VIEW maritime_data.development_noon_query;
DROP VIEW maritime_data.real_annual_query;
DROP TABLE maritime_data.quality_issues;
DROP TABLE maritime_data.synthetic_noon;
DROP TABLE maritime_data.annual_reports;
DROP TABLE maritime_data.synthetic_voyages;
DROP TABLE maritime_data.vessels;
DROP TABLE maritime_data.source_files;
DROP SCHEMA maritime_data; -- no CASCADE: unrelated objects must never be removed
