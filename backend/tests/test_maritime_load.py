"""The offline loader preserves existing app data and never initializes schemas."""
import csv
import json
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb
import pytest

from scripts.maritime_data.load_data import load_prepared, prepared_files, TABLES
from scripts.maritime_data.prepare_maritime_data import FIELDS
from test_maritime_migrations import postgres_dsn, apply_initial, apply_maritime


def prepared(directory: Path, *, invalid_annual=False, dangling_issue=False):
    directory.mkdir()
    rows = {table: [] for table in TABLES}
    rows["source_files"] = [{
        "source_id": "source-1", "dataset_id": "DS-005", "filename": "fixture.csv",
        "sha256": "a" * 64, "data_origin": "REAL", "granularity": "report",
        "selection": "REAL_ANNUAL_QUERY", "row_count": 1, "header_json": '["IMO"]',
    }]
    rows["vessels"] = [{
        "vessel_id": "REAL:IMO:9074729", "data_origin": "REAL",
        "imo_number": "9074729", "source_vessel_id": "9074729",
    }]
    if invalid_annual:
        rows["annual_reports"] = [{
            "record_id": "annual-1", "source_id": "source-1", "source_sheet": "CSV",
            "source_row": 2, "vessel_id": "REAL:IMO:9074729", "reporting_year": 2025,
            "report_type": "FULL", "quality_status": "REJECT", "aggregate_eligible": False,
            "provenance_json": "{}",  # Required period_label intentionally missing.
        }]
    if dangling_issue:
        rows["quality_issues"] = [{
            "issue_id": 1, "record_id": "missing-record", "source_id": "source-1",
            "source_row": 2, "severity": "REVIEW", "field": "fuel_t",
            "code": "fixture", "detail": "Unresolved record test",
        }]
    for table in TABLES:
        with (directory / (table + ".csv")).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=[f[0] for f in FIELDS[table]])
            writer.writeheader()
            writer.writerows(rows[table])
    counts = {table: len(values) for table, values in rows.items()}
    (directory / "summary.json").write_text(json.dumps({"rows": counts}), encoding="utf-8")
    return directory, counts


def migrated(dsn):
    apply_initial(dsn)
    apply_maritime(dsn)
    with psycopg.connect(dsn) as db:
        # The SQL migration harness mimics dbmate's ledger for loader validation.
        db.execute("CREATE TABLE schema_migrations(version varchar(255) PRIMARY KEY)")
        db.execute("INSERT INTO schema_migrations VALUES('20261013000000'),('20261013010000')")


def test_preview_validates_csv_without_connecting_to_database(tmp_path):
    directory, counts = prepared(tmp_path / "csv")
    assert load_prepared("invalid DSN that must not be used", directory) == {"mode": "preview", "rows": counts}


@pytest.mark.parametrize("problem", ["header", "count", "width", "boolean_count"])
def test_invalid_package_is_rejected_before_db_connection(tmp_path, problem):
    directory, _ = prepared(tmp_path / "csv")
    path = directory / "vessels.csv"
    if problem == "header":
        path.write_text(path.read_text().replace("source_vessel_id", "ship_id"), encoding="utf-8")
    elif problem == "width":
        with path.open("a") as stream:
            stream.write("only-one-column\n")
    else:
        summary_path = directory / "summary.json"
        summary = json.loads(summary_path.read_text())
        summary["rows"]["vessels"] = True if problem == "boolean_count" else 2
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(ValueError):
        load_prepared("invalid DSN", directory, apply=True)


def test_apply_requires_migration_ledger_without_creating_tables(postgres_dsn, tmp_path):
    directory, _ = prepared(tmp_path / "csv")
    with pytest.raises(ValueError, match="dbmate migrations"):
        load_prepared(postgres_dsn, directory, apply=True)
    with psycopg.connect(postgres_dsn) as db:
        assert db.execute("SELECT to_regclass('maritime_data.vessels')").fetchone()[0] is None


def test_four_column_vessel_csv_loads_without_creating_app_ships(postgres_dsn, tmp_path):
    migrated(postgres_dsn)
    body = {"id": "existing-ship", "imo": "9074729", "sample": False}
    with psycopg.connect(postgres_dsn) as db:
        db.execute("INSERT INTO ships(id,body,version) VALUES('existing-ship',%s,1)", (Jsonb(body),))
    directory, counts = prepared(tmp_path / "csv")
    assert load_prepared(postgres_dsn, directory, apply=True) == {"mode": "loaded", "rows": counts}
    with psycopg.connect(postgres_dsn) as db:
        assert db.execute("SELECT body FROM ships").fetchall() == [(body,)]
        assert db.execute("SELECT ship_id FROM maritime_data.vessels").fetchall() == [(None,)]
    with pytest.raises(ValueError, match="not empty"):
        load_prepared(postgres_dsn, directory, apply=True)
    with psycopg.connect(postgres_dsn) as db:
        assert db.execute("SELECT count(*) FROM maritime_data.vessels").fetchone()[0] == 1


@pytest.mark.parametrize("problem", ["invalid_annual", "dangling_issue"])
def test_any_late_failure_rolls_back_all_imported_tables(postgres_dsn, tmp_path, problem):
    migrated(postgres_dsn)
    directory, _ = prepared(tmp_path / "csv", **{problem: True})
    with pytest.raises((psycopg.Error, ValueError)):
        load_prepared(postgres_dsn, directory, apply=True)
    with psycopg.connect(postgres_dsn) as db:
        for table in TABLES:
            assert db.execute(f"SELECT count(*) FROM maritime_data.{table}").fetchone()[0] == 0
