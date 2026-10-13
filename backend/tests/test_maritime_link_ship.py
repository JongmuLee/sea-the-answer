"""The explicit mapping CLI cannot create ships, invent identities or overwrite links."""
from __future__ import annotations

import psycopg
from psycopg.rows import dict_row
import pytest

from api.store import Workspace
from api.errors import ApiError
from scripts.maritime_data import link_ship as cli
from test_maritime_restore_links import ADMIN, add_link, add_ship, maritime_table, postgres_dsn, ship


def source_rows(dsn):
    with Workspace(dsn) as db:
        return db.all("SELECT * FROM maritime_data.vessels ORDER BY vessel_id")


def map_vessel(dsn, vessel_id, ship_id, **options):
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        return cli.link_ship(connection, vessel_id, ship_id, **options)


def test_real_mapping_is_preview_first_then_idempotent_and_changes_only_link(postgres_dsn):
    with Workspace(postgres_dsn) as db:
        add_ship(db, ship(imo="IMO 9074729"))
        maritime_table(db)
        add_link(db, "REAL:IMO:9074729", None)
    before = source_rows(postgres_dsn)
    assert map_vessel(postgres_dsn, "REAL:IMO:9074729", "ship-1")["status"] == "preview"
    assert source_rows(postgres_dsn) == before
    assert map_vessel(postgres_dsn, "REAL:IMO:9074729", "ship-1", apply=True)["status"] == "linked"
    assert source_rows(postgres_dsn) == [{**before[0], "ship_id": "ship-1"}]
    assert map_vessel(postgres_dsn, "REAL:IMO:9074729", "ship-1", apply=True)["status"] == "already_linked"
    with Workspace(postgres_dsn) as db:
        assert db.one("SELECT count(*) AS total FROM ships")["total"] == 1


@pytest.mark.parametrize("changes", [{"imo": "1234567"}, {"sample": True}, {"imo": ""}])
def test_real_mapping_rejects_imo_mismatch_or_sample_ship(postgres_dsn, changes):
    with Workspace(postgres_dsn) as db:
        add_ship(db, ship(**changes))
        maritime_table(db)
        add_link(db, "REAL:IMO:9074729", None)
    with pytest.raises(cli.MappingError, match="same source/app IMO"):
        map_vessel(postgres_dsn, "REAL:IMO:9074729", "ship-1", apply=True)
    assert source_rows(postgres_dsn)[0]["ship_id"] is None


@pytest.mark.parametrize(("sample", "confirmed"), [(True, False), (False, True), (False, False)])
def test_synthetic_mapping_requires_confirmation_and_sample_ship(postgres_dsn, sample, confirmed):
    with Workspace(postgres_dsn) as db:
        add_ship(db, ship(imo="", sample=sample))
        maritime_table(db)
        add_link(db, "SYN:1", None, imo=None)
    with pytest.raises(cli.MappingError, match="confirm-synthetic"):
        map_vessel(postgres_dsn, "SYN:1", "ship-1", apply=True, confirm_synthetic=confirmed)
    assert source_rows(postgres_dsn)[0]["ship_id"] is None


def test_synthetic_mapping_is_explicit_and_does_not_populate_app_operations(postgres_dsn):
    with Workspace(postgres_dsn) as db:
        add_ship(db, ship(imo="", sample=True))
        maritime_table(db)
        add_link(db, "SYN:1", None, imo=None)
    result = map_vessel(postgres_dsn, "SYN:1", "ship-1", apply=True, confirm_synthetic=True)
    assert result["status"] == "linked"
    with Workspace(postgres_dsn) as db:
        assert db.one("SELECT count(*) AS total FROM ships")["total"] == 1
        assert db.one("SELECT count(*) AS total FROM operations")["total"] == 0


def test_mapping_refuses_replacement_and_duplicate_app_ship_links(postgres_dsn):
    with Workspace(postgres_dsn) as db:
        add_ship(db, ship(imo="", sample=True))
        add_ship(db, ship("ship-2", imo="", sample=True))
        maritime_table(db)
        add_link(db, "SYN:1", "ship-1", imo=None)
        add_link(db, "SYN:2", None, imo=None)
    before = source_rows(postgres_dsn)
    with pytest.raises(cli.MappingError, match="different link"):
        map_vessel(postgres_dsn, "SYN:1", "ship-2", apply=True, confirm_synthetic=True)
    with pytest.raises(cli.MappingError, match="another source vessel"):
        map_vessel(postgres_dsn, "SYN:2", "ship-1", apply=True, confirm_synthetic=True)
    assert source_rows(postgres_dsn) == before


def test_mapping_refuses_missing_app_ship_and_never_creates_it(postgres_dsn):
    with Workspace(postgres_dsn) as db:
        maritime_table(db)
        add_link(db, "REAL:IMO:9074729", None)
    with pytest.raises(cli.MappingError, match="app ship was not found"):
        map_vessel(postgres_dsn, "REAL:IMO:9074729", "missing-app-ship", apply=True)
    with Workspace(postgres_dsn) as db:
        assert db.one("SELECT count(*) AS total FROM ships")["total"] == 0


def test_cli_hides_connection_details_and_passwords(monkeypatch, capsys):
    secret = "test-password-must-not-appear"
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:" + secret + "@invalid.example/test")
    monkeypatch.setattr(cli, "load_dotenv", lambda *_args, **_kwargs: None)

    def fail_connection(*_args, **_kwargs):
        raise psycopg.OperationalError("connection failed for password " + secret)

    monkeypatch.setattr(cli.psycopg, "connect", fail_connection)
    assert cli.main(["--vessel-id", "REAL:IMO:9074729", "--ship-id", "ship-1"]) == 1
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err
    assert "invalid.example" not in captured.out + captured.err
    assert "Verify migrations" in captured.err


def test_cli_requires_database_url_without_falling_back_to_another_database(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("MARITIME_DATA_DATABASE_URL", "postgresql://must-not-use.example/test")
    monkeypatch.setattr(cli, "load_dotenv", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli.psycopg, "connect", lambda *_args, **_kwargs: pytest.fail("must not connect"))
    assert cli.main(["--vessel-id", "SYN:1", "--ship-id", "ship-1"]) == 1
    assert "DATABASE_URL is required" in capsys.readouterr().err


@pytest.mark.parametrize("changes", [{"imo": "1234567"}, {"imo": ""}, {"sample": True}])
def test_app_ship_save_cannot_invalidate_existing_real_mapping(postgres_dsn, changes):
    with Workspace(postgres_dsn) as db:
        add_ship(db, ship())
        maritime_table(db)
        add_link(db, "REAL:IMO:9074729", "ship-1")
    before = source_rows(postgres_dsn)
    with pytest.raises(ApiError) as error:
        with Workspace(postgres_dsn) as db:
            db.change(ADMIN, "ship.save", {**ship(**changes), "version": 1})
    assert error.value.status == 409
    assert error.value.code == "MARITIME_SHIP_IDENTITY_CONFLICT"
    assert source_rows(postgres_dsn) == before
    with Workspace(postgres_dsn) as db:
        row = db.one("SELECT body,version FROM ships WHERE id='ship-1'")
        assert row == {"body": ship(), "version": 1}
        assert db.one("SELECT count(*) AS total FROM changes")["total"] == 0


def test_app_ship_save_allows_real_name_and_capacity_changes_with_same_imo(postgres_dsn):
    with Workspace(postgres_dsn) as db:
        add_ship(db, ship())
        maritime_table(db)
        add_link(db, "REAL:IMO:9074729", "ship-1")
    before = source_rows(postgres_dsn)
    with Workspace(postgres_dsn) as db:
        result = db.change(ADMIN, "ship.save", {
            **ship(name="Renamed real vessel", dwt=55000, imo="IMO 9074729"), "version": 1})
        assert result["status"] == "approved"
    assert source_rows(postgres_dsn) == before
    with Workspace(postgres_dsn) as db:
        row = db.one("SELECT body,version FROM ships WHERE id='ship-1'")
        assert row["body"]["name"] == "Renamed real vessel"
        assert row["body"]["dwt"] == 55000
        assert row["version"] == 2


@pytest.mark.parametrize("changes", [
    {"name": "Different synthetic vessel"}, {"type": "Oil tanker"}, {"dwt": 55000},
    {"imo": "1234567"}, {"sample": False},
])
def test_app_ship_save_cannot_change_mapped_synthetic_identity(postgres_dsn, changes):
    current = ship(imo="", sample=True)
    with Workspace(postgres_dsn) as db:
        add_ship(db, current)
        maritime_table(db)
        add_link(db, "SYN:1", "ship-1", imo=None)
    before = source_rows(postgres_dsn)
    with pytest.raises(ApiError) as error:
        with Workspace(postgres_dsn) as db:
            db.change(ADMIN, "ship.save", {**current, **changes, "version": 1})
    assert error.value.status == 409 and error.value.code == "MARITIME_SHIP_IDENTITY_CONFLICT"
    assert source_rows(postgres_dsn) == before
    with Workspace(postgres_dsn) as db:
        assert db.one("SELECT body,version FROM ships WHERE id='ship-1'") == {"body": current, "version": 1}


@pytest.mark.parametrize("maritime_state", ["absent", "legacy", "unlinked"])
def test_app_ship_save_keeps_original_behavior_without_a_link(postgres_dsn, maritime_state):
    with Workspace(postgres_dsn) as db:
        add_ship(db, ship())
        if maritime_state == "legacy":
            db.run("CREATE SCHEMA maritime_data")
            db.run("CREATE TABLE maritime_data.vessels(vessel_id text PRIMARY KEY)")
        elif maritime_state == "unlinked":
            maritime_table(db)
            add_link(db, "REAL:IMO:9074729", None)
    with Workspace(postgres_dsn) as db:
        result = db.change(ADMIN, "ship.save", {
            **ship(imo="1234567", sample=True, name="Changed unlinked vessel"), "version": 1})
        assert result["status"] == "approved"
    with Workspace(postgres_dsn) as db:
        row = db.one("SELECT body,version FROM ships WHERE id='ship-1'")
        assert row["body"]["imo"] == "1234567" and row["body"]["sample"] is True
        assert row["version"] == 2


def test_app_ship_save_preserves_version_conflict_before_identity_check(postgres_dsn):
    with Workspace(postgres_dsn) as db:
        add_ship(db, ship())
        maritime_table(db)
        add_link(db, "REAL:IMO:9074729", "ship-1")
    with pytest.raises(ApiError) as error:
        with Workspace(postgres_dsn) as db:
            db.change(ADMIN, "ship.save", {**ship(imo="1234567"), "version": 0})
    assert error.value.code == "VERSION_CONFLICT"
