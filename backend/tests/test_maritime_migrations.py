"""Exercise the shipped migrations against isolated PostgreSQL databases.

Each test creates a random database owned by the fixture. The configured test
database is only used to create/drop those databases and is never migrated.
"""
from __future__ import annotations

import csv
from io import StringIO
import os
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb
import pytest

from scripts.maritime_data.prepare_maritime_data import FIELDS


ROOT = Path(__file__).resolve().parents[2]
INITIAL = ROOT / "migrations/20261013000000_init.sql"
MARITIME = ROOT / "migrations/20261013010000_maritime_data.sql"
TABLES = ("source_files", "vessels", "synthetic_voyages", "annual_reports",
          "synthetic_noon", "quality_issues")
VESSEL = "REAL:IMO:9074729"

# Frozen pre-feedback schema from the PR8 base, not the migration under test.
LEGACY_DDL = """
-- Maritime data v1. Does not modify the existing public app schema.

-- Review before application. Import only validated rows; this file does not load CSV.


CREATE SCHEMA IF NOT EXISTS maritime_data;

CREATE TABLE maritime_data.source_files (
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

CREATE TABLE maritime_data.vessels (
  vessel_id text PRIMARY KEY,
  data_origin text NOT NULL,
  imo_number text,
  source_vessel_id text NOT NULL
);

CREATE TABLE maritime_data.synthetic_voyages (
  voyage_id text PRIMARY KEY,
  vessel_id text NOT NULL REFERENCES maritime_data.vessels(vessel_id) ON DELETE RESTRICT,
  departure_port_label text,
  arrival_port_label text,
  first_report_date date NOT NULL,
  last_report_date date NOT NULL,
  mapping_status text NOT NULL
);

CREATE TABLE maritime_data.annual_reports (
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

CREATE TABLE maritime_data.synthetic_noon (
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

CREATE TABLE maritime_data.quality_issues (
  issue_id integer PRIMARY KEY,
  record_id text,
  source_id text NOT NULL REFERENCES maritime_data.source_files(source_id) ON DELETE RESTRICT,
  source_row integer NOT NULL,
  severity text NOT NULL,
  field text NOT NULL,
  code text NOT NULL,
  detail text NOT NULL
);

CREATE INDEX annual_vessel_year ON maritime_data.annual_reports(vessel_id, reporting_year);

CREATE INDEX noon_vessel_date ON maritime_data.synthetic_noon(vessel_id, report_date);

CREATE INDEX noon_voyage ON maritime_data.synthetic_noon(voyage_id);

CREATE VIEW maritime_data.real_annual_query AS SELECT * FROM maritime_data.annual_reports WHERE aggregate_eligible;

CREATE VIEW maritime_data.development_noon_query AS SELECT * FROM maritime_data.synthetic_noon WHERE quality_status='VALID';

"""


def migration_sql(path, direction="up"):
    source = path.read_text(encoding="utf-8")
    return source.split(f"-- migrate:{direction}", 1)[1].split("-- migrate:", 1)[0]


def execute(dsn, statement, params=None):
    """Match dbmate's one-transaction-per-file semantics, including rollback."""
    with psycopg.connect(dsn, connect_timeout=5) as db:
        db.execute(statement, params)


def apply_initial(dsn):
    execute(dsn, migration_sql(INITIAL))


def apply_maritime(dsn, direction="up"):
    execute(dsn, migration_sql(MARITIME, direction))


@pytest.fixture
def postgres_dsn():
    source = os.getenv("HAEDAP_TEST_DATABASE_URL", "")
    if not source:
        pytest.skip("HAEDAP_TEST_DATABASE_URL is required for migration integration tests")
    source = source.replace("postgresql+psycopg://", "postgresql://", 1)
    name = "haedap_migration_test_" + uuid4().hex
    with psycopg.connect(source, autocommit=True, connect_timeout=5) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
        try:
            yield make_conninfo(source, dbname=name)
        finally:
            # Only this fixture's literal prefix + random name can be dropped.
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def legacy_schema(dsn, schema="maritime_data"):
    """Install the historical DDL, including all original PK/FK/CHECK rules."""
    assert schema in {"role4", "maritime_data"}
    execute(dsn, LEGACY_DDL.replace("maritime_data", schema))

def seed_source_data(dsn, schema="maritime_data"):
    with psycopg.connect(dsn) as db:
        db.execute(sql.SQL("INSERT INTO {}.source_files "
                           "(source_id,dataset_id,filename,sha256,source_url,data_origin,granularity,"
                           "selection,row_count,header_json) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)").format(
                               sql.Identifier(schema)),
                   ("source-fixture", "DS-001", "fixture.csv", "a" * 64, None, "REAL", "report",
                    "migration regression fixture", 1, Jsonb(["IMO", "Fuel"])))
        db.execute(sql.SQL("INSERT INTO {}.vessels "
                           "(vessel_id,data_origin,imo_number,source_vessel_id) VALUES(%s,%s,%s,%s)").format(
                               sql.Identifier(schema)), (VESSEL, "REAL", "9074729", "9074729"))
        db.execute(sql.SQL("INSERT INTO {}.annual_reports "
                           "(record_id,source_id,source_sheet,source_row,vessel_id,reporting_year,"
                           "report_type,period_label,fuel_t,co2_t,quality_status,aggregate_eligible,provenance_json) "
                           "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)").format(sql.Identifier(schema)),
                   ("annual-fixture", "source-fixture", "CSV", 2, VESSEL, 2025, "FULL", "2025",
                    "123456789.123456789", "384444440.330444440946", "VALID", True,
                    Jsonb({"fuel_t": {"column": "Fuel", "missing": None}, "distance": "unverified"})))
        db.execute(sql.SQL("INSERT INTO {}.quality_issues "
                           "(issue_id,record_id,source_id,source_row,severity,field,code,detail) "
                           "VALUES(1,'annual-fixture','source-fixture',2,'INFO','distance','UNVERIFIED',%s)").format(
                               sql.Identifier(schema)), ("Original source quality detail remains intact.",))


def snapshot(dsn, schema="maritime_data"):
    """Compare every original field, including Decimal precision and provenance."""
    with psycopg.connect(dsn) as db:
        return {table: db.execute(sql.SQL("SELECT {} FROM {}.{} ORDER BY 1").format(
            sql.SQL(",").join(sql.Identifier(field[0]) for field in FIELDS[table]),
            sql.Identifier(schema), sql.Identifier(table))).fetchall() for table in TABLES}


def schema_exists(db, name):
    return db.execute("SELECT EXISTS(SELECT FROM pg_namespace WHERE nspname=%s)", (name,)).fetchone()[0]


def test_app_initial_migration_is_required_without_creating_maritime_schema(postgres_dsn):
    with pytest.raises(psycopg.errors.RaiseException, match="app init migration"):
        apply_maritime(postgres_dsn)
    with psycopg.connect(postgres_dsn) as db:
        assert not schema_exists(db, "maritime_data")


def test_fresh_schema_links_to_existing_app_ships_without_copying_operations(postgres_dsn):
    apply_initial(postgres_dsn)
    apply_maritime(postgres_dsn)
    with psycopg.connect(postgres_dsn) as db:
        assert db.execute("SELECT table_name FROM information_schema.tables "
                          "WHERE table_schema='maritime_data' AND table_type='BASE TABLE' ORDER BY table_name").fetchall() == [
                              (table,) for table in sorted(TABLES)]
        assert db.execute("SELECT table_name FROM information_schema.views "
                          "WHERE table_schema='maritime_data' ORDER BY table_name").fetchall() == [
                              ("development_noon_query",), ("real_annual_query",)]
        db.execute("INSERT INTO ships(id,body,version) VALUES('app-ship',%s,1)", (Jsonb({"imo": "9074729"}),))
        db.execute("INSERT INTO maritime_data.vessels VALUES(%s,'REAL','9074729','9074729','app-ship')", (VESSEL,))
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with db.transaction():
                db.execute("INSERT INTO maritime_data.vessels VALUES('REAL:IMO:9319466','REAL','9319466','9319466','missing')")
        with pytest.raises(psycopg.errors.UniqueViolation):
            with db.transaction():
                db.execute("INSERT INTO maritime_data.vessels VALUES('REAL:IMO:9319466','REAL','9319466','9319466','app-ship')")
        db.execute("DELETE FROM public.ships WHERE id='app-ship'")
        assert db.execute("SELECT ship_id,source_vessel_id FROM maritime_data.vessels").fetchone() == (None, "9074729")
        assert db.execute("SELECT count(*) FROM public.operations").fetchone()[0] == 0


@pytest.mark.parametrize("schema", ["maritime_data", "role4"])
def test_existing_csv_contract_is_adopted_and_all_source_fields_preserved(postgres_dsn, schema):
    apply_initial(postgres_dsn)
    legacy_schema(postgres_dsn, schema)
    seed_source_data(postgres_dsn, schema)
    before = snapshot(postgres_dsn, schema)
    apply_maritime(postgres_dsn)
    assert snapshot(postgres_dsn) == before
    with psycopg.connect(postgres_dsn) as db:
        assert not schema_exists(db, "role4")
        assert db.execute("SELECT ship_id FROM maritime_data.vessels").fetchall() == [(None,)]
        assert db.execute("SELECT record_id FROM maritime_data.real_annual_query").fetchall() == [("annual-fixture",)]
        assert db.execute("SELECT count(*) FROM public.ships").fetchone()[0] == 0


def test_both_schema_names_are_rejected_and_neither_is_modified(postgres_dsn):
    apply_initial(postgres_dsn)
    legacy_schema(postgres_dsn, "role4")
    seed_source_data(postgres_dsn, "role4")
    execute(postgres_dsn, "CREATE SCHEMA maritime_data")
    before = snapshot(postgres_dsn, "role4")
    with pytest.raises(psycopg.errors.RaiseException, match="Both role4 and maritime_data"):
        apply_maritime(postgres_dsn)
    assert snapshot(postgres_dsn, "role4") == before
    with psycopg.connect(postgres_dsn) as db:
        assert schema_exists(db, "maritime_data") and schema_exists(db, "role4")
        assert db.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='maritime_data'").fetchone()[0] == 0


def test_partial_existing_schema_is_rejected_without_creating_missing_tables(postgres_dsn):
    apply_initial(postgres_dsn)
    execute(postgres_dsn, "CREATE SCHEMA maritime_data; CREATE TABLE maritime_data.vessels "
                         "(vessel_id text PRIMARY KEY,data_origin text NOT NULL,imo_number text,source_vessel_id text NOT NULL); "
                         "INSERT INTO maritime_data.vessels VALUES('SYN:kept','SYNTHETIC',NULL,'kept')")
    with pytest.raises(psycopg.errors.RaiseException, match="Unsupported existing maritime table"):
        apply_maritime(postgres_dsn)
    with psycopg.connect(postgres_dsn) as db:
        assert db.execute("SELECT * FROM maritime_data.vessels").fetchall() == [("SYN:kept", "SYNTHETIC", None, "kept")]
        assert db.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='maritime_data'").fetchone()[0] == 1


@pytest.mark.parametrize("schema", ["maritime_data", "role4"])
def test_incompatible_columns_are_rejected_and_legacy_rename_is_rolled_back(postgres_dsn, schema):
    apply_initial(postgres_dsn)
    legacy_schema(postgres_dsn, schema)
    seed_source_data(postgres_dsn, schema)
    execute(postgres_dsn, sql.SQL("ALTER TABLE {}.vessels ALTER COLUMN imo_number TYPE integer USING imo_number::integer").format(
        sql.Identifier(schema)))
    before = snapshot(postgres_dsn, schema)
    with pytest.raises(psycopg.errors.RaiseException, match="Unsupported existing maritime columns"):
        apply_maritime(postgres_dsn)
    assert snapshot(postgres_dsn, schema) == before
    with psycopg.connect(postgres_dsn) as db:
        assert schema_exists(db, schema)
        if schema == "role4":
            assert not schema_exists(db, "maritime_data")


@pytest.mark.parametrize("mutation", [
    "ALTER TABLE maritime_data.quality_issues DROP CONSTRAINT quality_issues_pkey",
    "ALTER TABLE maritime_data.annual_reports DROP CONSTRAINT annual_reports_source_id_fkey",
    "ALTER TABLE maritime_data.annual_reports DROP CONSTRAINT annual_reports_quality_status_check",
    "ALTER TABLE maritime_data.annual_reports DROP CONSTRAINT annual_reports_source_id_fkey; "
    "ALTER TABLE maritime_data.annual_reports ADD CONSTRAINT annual_reports_source_id_fkey "
    "FOREIGN KEY(source_id) REFERENCES maritime_data.source_files(source_id) ON DELETE CASCADE",
    "ALTER TABLE maritime_data.annual_reports DROP CONSTRAINT annual_reports_source_id_fkey; "
    "ALTER TABLE maritime_data.annual_reports ADD CONSTRAINT annual_reports_source_id_fkey "
    "FOREIGN KEY(source_id) REFERENCES maritime_data.source_files(source_id) ON DELETE RESTRICT NOT VALID",
    "CREATE TABLE public.wrong_source_target(source_id text PRIMARY KEY); "
    "INSERT INTO public.wrong_source_target VALUES('source-fixture'); "
    "ALTER TABLE maritime_data.annual_reports DROP CONSTRAINT annual_reports_source_id_fkey; "
    "ALTER TABLE maritime_data.annual_reports ADD CONSTRAINT annual_reports_source_id_fkey "
    "FOREIGN KEY(source_id) REFERENCES public.wrong_source_target(source_id) ON DELETE RESTRICT",
    "ALTER TABLE maritime_data.synthetic_noon DROP CONSTRAINT synthetic_noon_engine_hours_check; "
    "ALTER TABLE maritime_data.synthetic_noon ADD CONSTRAINT synthetic_noon_engine_hours_check "
    "CHECK(engine_hours BETWEEN 0 AND 25)",
], ids=["missing-primary-key", "missing-foreign-key", "missing-check", "wrong-delete-action",
        "unvalidated-foreign-key", "wrong-reference-target", "changed-check-expression"])
def test_incompatible_constraints_are_rejected_without_modifying_source_data(postgres_dsn, mutation):
    apply_initial(postgres_dsn)
    legacy_schema(postgres_dsn)
    seed_source_data(postgres_dsn)
    execute(postgres_dsn, mutation)
    before = snapshot(postgres_dsn)
    with pytest.raises(psycopg.errors.RaiseException, match="Unsupported existing maritime"):
        apply_maritime(postgres_dsn)
    assert snapshot(postgres_dsn) == before
    with psycopg.connect(postgres_dsn) as db:
        assert db.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema='maritime_data' "
                          "AND table_name='vessels' AND column_name='ship_id'").fetchone()[0] == 0


def test_unreviewed_existing_ship_link_column_is_not_silently_adopted(postgres_dsn):
    apply_initial(postgres_dsn)
    legacy_schema(postgres_dsn)
    seed_source_data(postgres_dsn)
    execute(postgres_dsn, "ALTER TABLE maritime_data.vessels ADD COLUMN ship_id text")
    before = snapshot(postgres_dsn)
    with pytest.raises(psycopg.errors.RaiseException, match="Unsupported existing maritime columns"):
        apply_maritime(postgres_dsn)
    assert snapshot(postgres_dsn) == before


def test_equivalent_legacy_constraints_may_have_different_names(postgres_dsn):
    apply_initial(postgres_dsn)
    legacy_schema(postgres_dsn)
    seed_source_data(postgres_dsn)
    execute(postgres_dsn, "ALTER TABLE maritime_data.quality_issues RENAME CONSTRAINT quality_issues_pkey TO old_quality_pk; "
                         "ALTER TABLE maritime_data.annual_reports RENAME CONSTRAINT annual_reports_source_id_fkey TO old_source_fk; "
                         "ALTER TABLE maritime_data.annual_reports RENAME CONSTRAINT annual_reports_quality_status_check TO old_status_check")
    before = snapshot(postgres_dsn)
    apply_maritime(postgres_dsn)
    assert snapshot(postgres_dsn) == before


def test_populated_down_is_rejected_and_source_data_is_preserved(postgres_dsn):
    apply_initial(postgres_dsn)
    apply_maritime(postgres_dsn)
    seed_source_data(postgres_dsn)
    before = snapshot(postgres_dsn)
    with pytest.raises(psycopg.errors.RaiseException, match="not empty"):
        apply_maritime(postgres_dsn, "down")
    assert snapshot(postgres_dsn) == before
    with psycopg.connect(postgres_dsn) as db:
        assert db.execute("SELECT count(*) FROM maritime_data.real_annual_query").fetchone()[0] == 1


def test_empty_down_and_reapply_leave_app_data_untouched(postgres_dsn):
    apply_initial(postgres_dsn)
    execute(postgres_dsn, "INSERT INTO ships(id,body,version) VALUES('keep-app-ship','{}',1)")
    apply_maritime(postgres_dsn)
    apply_maritime(postgres_dsn, "down")
    with psycopg.connect(postgres_dsn) as db:
        assert not schema_exists(db, "maritime_data")
        assert db.execute("SELECT id FROM public.ships").fetchall() == [("keep-app-ship",)]
    apply_maritime(postgres_dsn)
    with psycopg.connect(postgres_dsn) as db:
        assert db.execute("SELECT count(*) FROM maritime_data.vessels").fetchone()[0] == 0
        assert db.execute("SELECT id FROM public.ships").fetchall() == [("keep-app-ship",)]


def test_prepared_four_column_vessels_csv_loads_with_new_nullable_ship_link(postgres_dsn):
    apply_initial(postgres_dsn)
    apply_maritime(postgres_dsn)
    fields = [field[0] for field in FIELDS["vessels"]]
    assert fields == ["vessel_id", "data_origin", "imo_number", "source_vessel_id"]
    payload = StringIO(newline="")
    writer = csv.writer(payload)
    writer.writerow(fields)
    writer.writerow([VESSEL, "REAL", "9074729", "9074729"])
    writer.writerow(["SYN:fixture", "SYNTHETIC", "", "fixture"])
    with psycopg.connect(postgres_dsn) as db:
        columns = db.execute("SELECT column_name,is_nullable FROM information_schema.columns "
                             "WHERE table_schema='maritime_data' AND table_name='vessels' ORDER BY ordinal_position").fetchall()
        assert [name for name, _nullable in columns] == fields + ["ship_id"]
        assert columns[-1] == ("ship_id", "YES")
        command = sql.SQL("COPY maritime_data.vessels ({}) FROM STDIN WITH (FORMAT csv, HEADER true, NULL '')").format(
            sql.SQL(",").join(sql.Identifier(field) for field in fields))
        with db.cursor().copy(command) as copy:
            copy.write(payload.getvalue())
        assert db.execute("SELECT vessel_id,imo_number,ship_id FROM maritime_data.vessels ORDER BY vessel_id").fetchall() == [
            (VESSEL, "9074729", None), ("SYN:fixture", None, None)]
