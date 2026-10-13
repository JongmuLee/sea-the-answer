"""Preview or atomically load prepared CSVs into the migrated app PostgreSQL DB."""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

import psycopg
from psycopg import sql
from dotenv import load_dotenv

from .prepare_maritime_data import FIELDS

TABLES = ("source_files", "vessels", "synthetic_voyages", "annual_reports",
          "synthetic_noon", "quality_issues")
MIGRATION = "20261013010000"


def prepared_files(directory: Path) -> tuple[dict, dict]:
    """Validate all headers and counts before opening a writable transaction."""
    directory = directory.resolve(strict=True)
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8-sig"))
    expected = summary.get("rows")
    if (not isinstance(expected, dict) or set(expected) != set(TABLES)
            or any(type(n) is not int or n < 0 for n in expected.values())):
        raise ValueError("summary.json must contain integer counts for all six tables")
    paths = {}
    for table in TABLES:
        path = directory / (table + ".csv")
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = csv.reader(stream)
            if next(rows, None) != [field[0] for field in FIELDS[table]]:
                raise ValueError(f"Unexpected CSV header: {table}")
            count = 0
            for row in rows:
                if len(row) != len(FIELDS[table]):
                    raise ValueError(f"Unexpected CSV width: {table}, logical row {count + 2}")
                count += 1
        if count != expected[table]:
            raise ValueError(f"CSV/summary row count mismatch: {table}")
        paths[table] = path
    return paths, expected


def verify_records(connection, expected: dict) -> None:
    for table, count in expected.items():
        actual = connection.execute(sql.SQL("SELECT count(*) FROM maritime_data.{}")
                                    .format(sql.Identifier(table))).fetchone()[0]
        if actual != count:
            raise ValueError(f"Loaded row count mismatch: {table}")
    inconsistent = connection.execute("""
        SELECT EXISTS (
          SELECT FROM maritime_data.synthetic_noon n
          JOIN maritime_data.synthetic_voyages v USING(voyage_id)
          WHERE n.vessel_id<>v.vessel_id
        ) OR EXISTS (
          SELECT FROM maritime_data.annual_reports a
          JOIN maritime_data.vessels v USING(vessel_id)
          JOIN maritime_data.source_files s USING(source_id)
          WHERE v.data_origin<>'REAL' OR s.data_origin<>'REAL'
        ) OR EXISTS (
          SELECT FROM maritime_data.synthetic_noon n
          JOIN maritime_data.vessels v USING(vessel_id)
          JOIN maritime_data.source_files s USING(source_id)
          WHERE v.data_origin<>'SYNTHETIC' OR s.data_origin<>'SYNTHETIC'
        ) OR EXISTS (
          SELECT FROM maritime_data.quality_issues q
          WHERE q.record_id IS NOT NULL
          AND NOT EXISTS (SELECT FROM maritime_data.annual_reports a WHERE a.record_id=q.record_id)
          AND NOT EXISTS (SELECT FROM maritime_data.synthetic_noon n WHERE n.record_id=q.record_id)
        )
    """).fetchone()[0]
    if inconsistent:
        raise ValueError("Origin, voyage or quality provenance reference mismatch")


def load_prepared(dsn: str, directory: Path, *, apply: bool = False) -> dict:
    paths, expected = prepared_files(directory)
    result = {"mode": "preview", "rows": expected}
    if not apply:
        return result
    if not dsn:
        raise ValueError("DATABASE_URL is required; run migrations before loading")
    dsn = dsn.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(dsn, connect_timeout=5) as connection:
        connection.execute("SET LOCAL lock_timeout='5s'")
        connection.execute("SET LOCAL statement_timeout='120s'")
        if connection.execute("SELECT to_regclass('public.schema_migrations')").fetchone()[0] is None:
            raise ValueError("Apply dbmate migrations before loading")
        if not connection.execute("SELECT 1 FROM public.schema_migrations WHERE version=%s",
                                  (MIGRATION,)).fetchone():
            raise ValueError("Maritime dbmate migration is not applied")
        if connection.execute("SELECT EXISTS (SELECT FROM pg_namespace WHERE nspname='role4')").fetchone()[0]:
            raise ValueError("Legacy role4 schema remains; resolve migrations before loading")
        connection.execute(sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE")
                           .format(sql.SQL(",").join(sql.Identifier("maritime_data", t) for t in TABLES)))
        for table in TABLES:
            if connection.execute(sql.SQL("SELECT EXISTS (SELECT FROM maritime_data.{})")
                                  .format(sql.Identifier(table))).fetchone()[0]:
                raise ValueError("Maritime tables are not empty; refusing overwrite or reload")
        for table, path in paths.items():
            columns = sql.SQL(",").join(sql.Identifier(f[0]) for f in FIELDS[table])
            copy_sql = sql.SQL("COPY maritime_data.{} ({}) FROM STDIN WITH (FORMAT CSV, HEADER TRUE, NULL '')")
            with connection.cursor().copy(copy_sql.format(sql.Identifier(table), columns)) as copy:
                with path.open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        copy.write(chunk)
        verify_records(connection, expected)
    return {**result, "mode": "loaded"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=Path(__file__).resolve().parents[1] / ".env")
    parser.add_argument("--apply", action="store_true", help="Write only to empty, migrated maritime tables")
    args = parser.parse_args()
    load_dotenv(args.env_file, override=False)
    try:
        print(json.dumps(load_prepared(os.getenv("DATABASE_URL", ""), args.data_dir, apply=args.apply),
                         ensure_ascii=False))
    except (ValueError, OSError, psycopg.Error):
        # Driver errors can include source rows and connection details.
        raise SystemExit("Load failed. Check input headers/counts, migrations and empty tables; transaction rolled back.")


if __name__ == "__main__":
    main()
