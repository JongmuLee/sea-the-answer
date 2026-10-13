"""App backup compatibility and preservation of explicit maritime ship links."""
from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb
import pytest

from api.store import Workspace
from api.store.backups import BACKUP_TABLES, BackupStore


ADMIN = {"id": "restore-admin", "username": "restore-admin", "name": "복구 테스트 관리자",
         "role": "admin", "active": True, "version": 1}


def ship(ship_id="ship-1", **changes):
    return {"id": ship_id, "name": "Test vessel", "type": "Bulk carrier", "dwt": 50000,
            "imo": "9074729", "sample": False, "from": "Busan", "to": "Singapore", **changes}


@pytest.mark.parametrize(("source_imo", "before", "after", "matches"), [
    ("9074729", ship(), ship(imo="IMO 9074729", name="Renamed vessel"), True),
    ("9074729", ship(), ship(imo="1234567"), False),
    ("9074729", ship(), ship(sample=True), False),
    ("9074729", ship(), ship(imo=""), False),
    (None, ship(imo="", sample=True), ship(imo="", sample=True, to="Tokyo"), True),
    (None, ship(imo="", sample=True), ship(imo="", sample=True, name="Other vessel"), False),
    (None, ship(imo="", sample=True), ship(imo="", sample=False), False),
    (None, {}, {}, False),
    ("9074729", ship(), None, False),
])
def test_restore_requires_matching_vessel_identity(source_imo, before, after, matches):
    link = {"vessel_id": "REAL:IMO:" + source_imo if source_imo else "SYN:1",
            "imo_number": source_imo, "body": before}
    assert BackupStore._maritime_ship_matches(link, after) is matches


def test_restore_cannot_treat_real_vessel_without_imo_as_synthetic():
    link = {"vessel_id": "REAL:IMO:9074729", "imo_number": None, "body": ship()}
    assert not BackupStore._maritime_ship_matches(link, ship())


@pytest.fixture
def postgres_dsn():
    """Create and remove only an owned test database, never truncate the supplied DB."""
    source = os.getenv("HAEDAP_TEST_DATABASE_URL", "")
    if not source:
        pytest.skip("HAEDAP_TEST_DATABASE_URL is required for PostgreSQL restore integration tests")
    source = source.replace("postgresql+psycopg://", "postgresql://", 1)
    name = "haedap_restore_test_" + uuid4().hex
    with psycopg.connect(source, autocommit=True, connect_timeout=5) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
        try:
            dsn = make_conninfo(source, dbname=name)
            initial = Path(__file__).resolve().parents[2] / "migrations/20261013000000_init.sql"
            up = initial.read_text(encoding="utf-8").split("-- migrate:up", 1)[1].split("-- migrate:down", 1)[0]
            with psycopg.connect(dsn) as setup:
                setup.execute(up)
                setup.execute("""INSERT INTO users(id,username,name,role,password,active,version)
                    VALUES(%s,%s,%s,'admin','test-only',true,1)""",
                              (ADMIN["id"], ADMIN["username"], ADMIN["name"]))
            yield dsn
        finally:
            # The random name is constructed here and never comes from configuration.
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def add_ship(db, body):
    db.run("INSERT INTO ships(id,body,version) VALUES(%s,%s,1)", (body["id"], Jsonb(body)))


def maritime_table(db):
    # The production FK action is essential: a public restore clears these links.
    db.run("CREATE SCHEMA maritime_data")
    db.run("""CREATE TABLE maritime_data.vessels (
        vessel_id text PRIMARY KEY, data_origin text NOT NULL, imo_number text,
        source_vessel_id text NOT NULL,
        ship_id text REFERENCES public.ships(id) ON DELETE SET NULL,
        source_payload text NOT NULL DEFAULT 'preserved-source-data'
    )""")


def add_link(db, vessel_id, ship_id, imo="9074729"):
    db.run("""INSERT INTO maritime_data.vessels
        (vessel_id,data_origin,imo_number,source_vessel_id,ship_id)
        VALUES(%s,%s,%s,%s,%s)""",
           (vessel_id, "REAL" if imo else "SYNTHETIC", imo, "source-" + vessel_id, ship_id))


def backup(dsn, tmp_path):
    with Workspace(dsn) as db:
        return db.create_backup(ADMIN, tmp_path)


def restore(dsn, tmp_path, saved):
    with Workspace(dsn) as db:
        return db.restore_backup(ADMIN, tmp_path, {"id": saved["id"], "confirm": "복구"})


def test_app_only_backup_remains_compatible_without_maritime_schema(postgres_dsn, tmp_path):
    with Workspace(postgres_dsn) as db:
        add_ship(db, ship())
    saved = backup(postgres_dsn, tmp_path)
    with Workspace(postgres_dsn) as db:
        snapshot = db.read_backup(ADMIN, tmp_path, saved["id"])
        assert snapshot["version"] == 2
        assert set(snapshot["tables"]) == set(BACKUP_TABLES)
        db.run("UPDATE ships SET body=%s WHERE id='ship-1'", (Jsonb(ship(name="Changed")),))
    result = restore(postgres_dsn, tmp_path, saved)
    assert result["ok"] and "maritimeLinks" not in result and "warnings" not in result
    with Workspace(postgres_dsn) as db:
        assert db.one("SELECT body FROM ships WHERE id='ship-1'")["body"] == ship()


def test_restore_leaves_legacy_maritime_table_without_ship_id_untouched(postgres_dsn, tmp_path):
    with Workspace(postgres_dsn) as db:
        add_ship(db, ship())
        db.run("CREATE SCHEMA maritime_data")
        db.run("CREATE TABLE maritime_data.vessels(vessel_id text PRIMARY KEY, source_payload text)")
        db.run("INSERT INTO maritime_data.vessels VALUES('SYN:legacy','unchanged')")
    saved = backup(postgres_dsn, tmp_path)
    result = restore(postgres_dsn, tmp_path, saved)
    assert result["ok"] and "maritimeLinks" not in result
    with Workspace(postgres_dsn) as db:
        assert db.all("SELECT * FROM maritime_data.vessels") == [
            {"vessel_id": "SYN:legacy", "source_payload": "unchanged"}]


def test_restore_preserves_only_explicit_links_and_keeps_source_rows(postgres_dsn, tmp_path):
    with Workspace(postgres_dsn) as db:
        add_ship(db, ship())
        add_ship(db, ship("synthetic-1", imo="", sample=True))
        maritime_table(db)
        add_link(db, "REAL:IMO:9074729", "ship-1")
        add_link(db, "SYN:1", "synthetic-1", imo=None)
        add_link(db, "SYN:unlinked", None, imo=None)
    saved = backup(postgres_dsn, tmp_path)
    with Workspace(postgres_dsn) as db:
        # A port change is operational context, not a different vessel identity.
        db.run("UPDATE ships SET body=%s WHERE id='synthetic-1'",
               (Jsonb(ship("synthetic-1", imo="", sample=True, to="Tokyo")),))
        before = db.all("SELECT * FROM maritime_data.vessels ORDER BY vessel_id")
    result = restore(postgres_dsn, tmp_path, saved)
    assert result["maritimeLinks"] == {
        "linkedBefore": 2, "restored": 2, "cleared": 0, "missingShips": 0, "identityMismatch": 0}
    assert "warnings" not in result
    with Workspace(postgres_dsn) as db:
        assert db.all("SELECT * FROM maritime_data.vessels ORDER BY vessel_id") == before
        snapshot = db.read_backup(ADMIN, tmp_path, saved["id"])
        assert set(snapshot["tables"]) == set(BACKUP_TABLES)
        audit = db.one("SELECT detail FROM audit WHERE action='전체 복구'")["detail"]
        assert audit["maritimeLinks"] == result["maritimeLinks"]


def test_restore_clears_missing_or_reused_ship_ids_and_reports_counts(postgres_dsn, tmp_path):
    with Workspace(postgres_dsn) as db:
        # The backup's reused app ID refers to a different real vessel.
        add_ship(db, ship(imo="1234567"))
        maritime_table(db)
    saved = backup(postgres_dsn, tmp_path)
    with Workspace(postgres_dsn) as db:
        db.run("UPDATE ships SET body=%s WHERE id='ship-1'", (Jsonb(ship()),))
        add_ship(db, ship("missing-in-backup", imo="9319466"))
        add_link(db, "REAL:IMO:9074729", "ship-1")
        add_link(db, "REAL:IMO:9319466", "missing-in-backup", imo="9319466")
        before = db.all("SELECT * FROM maritime_data.vessels ORDER BY vessel_id")
    result = restore(postgres_dsn, tmp_path, saved)
    assert result["maritimeLinks"] == {
        "linkedBefore": 2, "restored": 0, "cleared": 2, "missingShips": 1, "identityMismatch": 1}
    assert len(result["warnings"]) == 1 and "2건" in result["warnings"][0]
    with Workspace(postgres_dsn) as db:
        after = db.all("SELECT * FROM maritime_data.vessels ORDER BY vessel_id")
        assert after == [{**row, "ship_id": None} for row in before]
        assert db.one("SELECT detail FROM audit WHERE action='전체 복구'")["detail"]["maritimeLinks"] == result["maritimeLinks"]


def test_restore_does_not_reconnect_synthetic_ship_with_changed_identity(postgres_dsn, tmp_path):
    with Workspace(postgres_dsn) as db:
        add_ship(db, ship(imo="", sample=True))
        maritime_table(db)
    saved = backup(postgres_dsn, tmp_path)
    with Workspace(postgres_dsn) as db:
        db.run("UPDATE ships SET body=%s WHERE id='ship-1'",
               (Jsonb(ship(imo="", sample=True, name="Different synthetic ship")),))
        add_link(db, "SYN:1", "ship-1", imo=None)
    result = restore(postgres_dsn, tmp_path, saved)
    assert result["maritimeLinks"]["identityMismatch"] == 1
    with Workspace(postgres_dsn) as db:
        assert db.one("SELECT ship_id FROM maritime_data.vessels WHERE vessel_id='SYN:1'")["ship_id"] is None


def test_failed_restore_rolls_back_public_data_and_maritime_fk_changes(postgres_dsn, tmp_path, monkeypatch):
    with Workspace(postgres_dsn) as db:
        add_ship(db, ship())
        maritime_table(db)
        add_link(db, "REAL:IMO:9074729", "ship-1")
    saved = backup(postgres_dsn, tmp_path)
    with Workspace(postgres_dsn) as db:
        db.run("UPDATE ships SET body=%s WHERE id='ship-1'", (Jsonb(ship(name="Current name")),))
    original_run = Workspace.run

    def fail_ship_insert(self, query, params=()):
        if query.startswith("INSERT INTO ships("):
            raise RuntimeError("simulated restore insertion failure")
        return original_run(self, query, params)

    monkeypatch.setattr(Workspace, "run", fail_ship_insert)
    with pytest.raises(RuntimeError, match="simulated restore"):
        restore(postgres_dsn, tmp_path, saved)
    with Workspace(postgres_dsn) as db:
        assert db.one("SELECT body FROM ships WHERE id='ship-1'")["body"]["name"] == "Current name"
        assert db.one("SELECT ship_id FROM maritime_data.vessels")["ship_id"] == "ship-1"
