"""AIS/berth references retain identity, units, time and existing API boundaries."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from unittest.mock import MagicMock

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from tools import api
from tools.navigation_references import (
    NavigationCatalog, NavigationScreenRequest, load_catalog, screen_references, screen_from_database,
)
from scripts.prepare_navigation_references import ais_observations, berth_references, listed_number
from scripts.prepare_navigation_references import URLS, captured, digest, prepare
from scripts.prepare_navigation_references import dump
from tools.navigation_references import catalog_summary
from scripts.prepare_port_references import PORT_NUMBERS

IMO = "7422881"
VESSEL = "REAL:IMO:" + IMO
PORT = "WPI:24020"
BERTH = PORT + ":BERTH:510"
TOKEN = "navigation-test-only-token-12345678901234567890"


def test_subheadings_do_not_drop_berth_conditions_or_contaminate_the_next_berth():
    html = ('<h5>Berth 510</h5><p>Max. LOA: 100 metres</p><h6>Restrictions</h6>'
            '<p>Tug approval required.</p><h5>Berth 511</h5><p>Max. LOA: 80 metres</p>')
    first, second = berth_references(html, PORT)
    assert 'Restrictions' in first['conditions'][0] and 'Tug approval required.' in first['conditions'][0]
    assert first['max_loa_m'] == '100' and second['max_loa_m'] == '80'
    assert 'Tug approval' not in second['conditions'][0]


def test_snapshot_replace_failure_preserves_previous_complete_catalog(monkeypatch, tmp_path):
    path = tmp_path / 'catalog.json'
    dump(path, {'previous': True})
    before = path.read_bytes()
    def fail(*args):
        raise OSError('replacement failed')
    monkeypatch.setattr('scripts.prepare_navigation_references.os.replace', fail)
    with pytest.raises(OSError):
        dump(path, {'new': True})
    assert path.read_bytes() == before and list(tmp_path.iterdir()) == [path]


def test_catalog_reports_record_age_separately_from_capture_age():
    data = catalog_data()
    data['observations'][0]['metadata_received_at'] = (NOW - timedelta(days=2)).isoformat()
    summary = catalog_summary(NavigationCatalog.model_validate(data), 'a' * 64, now=NOW)
    assert summary['freshness']['ais_capture_issue'] is None
    assert summary['freshness']['ais_record_age_counts'] == {'AIS_RECORD_STALE': 1}
    assert summary['freshness']['port_effective_date_verified'] is False
    assert 'observations' not in summary
NOW = datetime.now(timezone.utc)


def ais(**changes):
    return {"imo": int(IMO), "mmsi": 123456789, "name": "Example",
            "timestamp": int(NOW.timestamp() * 1000), "referencePointA": 70,
            "referencePointB": 30, "referencePointC": 5, "referencePointD": 15,
            "draught": 65, **changes}


def catalog_data():
    return {"version": "NAVIGATION-REFERENCES-1.0", "generated_at": NOW.isoformat(),
        "mrv_vessels_sha256": "a" * 64, "wpi_sha256": "b" * 64,
        "sources": [{"id": key, "url": "https://example.org/" + key, "sha256": "c" * 64,
            "retrieved_at": NOW.isoformat(), "effective_at": None, "attribution": "test source",
            "license_url": None} for key in ("ais", "schema", "berths")],
        "observations": ais_observations([ais()], {IMO}),
        "ports": [{"port_id": PORT, "name": "Example port", "unlocode": "SEGOT",
            "berths": berth_references(
                '<h5>Berth 510</h5><p>Max. LOA: 100 metres</p><p>Max. draught: 6.5 metres</p>', PORT)}]}


def request(**changes):
    return NavigationScreenRequest(vessel_id=VESSEL, port_id=PORT, **changes)


def test_ais_units_and_derived_dimensions_are_exact_and_keep_source_pointer():
    raw = ais()
    before = deepcopy(raw)
    r = ais_observations([raw], {IMO})[0]
    assert (r["reported_length_m"], r["reported_beam_m"], r["reported_draught_m"]) == ("100", "20", "6.5")
    assert r["metadata_received_at"].endswith("+00:00")
    assert r["source_ref"] == {"source_id": "ais", "locator": "/0"}
    assert raw == before


@pytest.mark.parametrize("value", [0, -1, 256, True, "65", 65.0, None])
def test_ais_draught_special_values_do_not_become_exact_metres(value):
    result = ais_observations([ais(draught=value)], {IMO})[0]
    assert result["reported_draught_m"] is None


def test_ais_255_is_only_a_lower_bound_not_25_point_5_exact():
    result = ais_observations([ais(draught=255)], {IMO})[0]
    assert result["reported_draught_m"] is None
    assert result["draught_lower_bound_m"] == "25.5"


@pytest.mark.parametrize("field,value", [("referencePointA", 0), ("referencePointB", 511),
                                         ("referencePointC", 63), ("referencePointD", -1)])
def test_ambiguous_offset_boundaries_are_not_summed(field, value):
    r = ais_observations([ais(**{field: value})], {IMO})[0]
    assert r["reported_length_m"] is None or r["reported_beam_m"] is None


def test_only_exact_valid_imo_joins_and_identity_conflicts_are_preserved():
    assert ais_observations([ais(imo=7422880), ais(imo=0)], {IMO}) == []
    assert ais_observations([ais()], set()) == []
    rows = ais_observations([ais(), ais(mmsi=234567890)], {IMO})
    assert len(rows) == 2 and all("AIS_IDENTITY_AMBIGUOUS" in r["issues"] for r in rows)
    # Conflicts outside the prepared MRV population are still noticed before filtering.
    rows = ais_observations([ais(), ais(imo=9074729)], {IMO})
    assert "AIS_IDENTITY_AMBIGUOUS" in rows[0]["issues"]


def test_seconds_timestamp_is_not_silently_converted_as_milliseconds():
    result = ais_observations([ais(timestamp=int(NOW.timestamp()))], {IMO})[0]
    assert result["metadata_received_at"] is None


@pytest.mark.parametrize("value", ["6.5-7.5 metres", "6,5 (6,0) meters", "6.5", "0 metres",
                                    "19.05 metres in density 1.013", "400 metres (after agreement)"])
def test_conditions_ranges_and_missing_units_are_not_flattened(value):
    assert listed_number(value) is None


def test_berth_semantics_and_duplicate_sections_block_selection():
    html = '<h5>Berth 510</h5><p>Length: 500 metres</p><p>Max. draught: 6,5 metres</p>'
    b = berth_references(html, PORT)[0]
    assert b["max_loa_m"] is None  # Physical berth length is not permitted vessel LOA.
    assert b["max_draught_m"] == "6.5" and b["max_beam_m"] is None
    duplicates = berth_references(html + '<h5>Berth 510</h5><p>Max. draught: 7.5 metres</p>', PORT)
    assert len(duplicates) == 1 and len(duplicates[0]["variants"]) == 2
    assert duplicates[0]["max_draught_m"] is None
    assert "DUPLICATE_BERTH_VARIANTS_REQUIRE_REVIEW" in duplicates[0]["issues"]


def test_berth_parser_does_not_execute_or_read_script_headings_and_never_truncates_conditions():
    html = '<script><h5>Berth 999</h5>Max. LOA: 999 metres</script><h5>Berth 510</h5><p>Max. LOA: 100 metres</p>'
    assert [b["label"] for b in berth_references(html, PORT)] == ["510"]
    with pytest.raises(ValueError, match="truncate"):
        berth_references('<h5>Berth 1</h5><p>' + "x" * 6001 + '</p>', PORT)
    with pytest.raises(ValueError):
        berth_references('<p>layout changed</p>', PORT)


def test_equal_reference_values_are_partial_checks_and_never_admission_permissions():
    catalog = NavigationCatalog.model_validate(catalog_data())
    before = catalog.model_dump_json()
    result = screen_references(catalog, request(), now=NOW)
    berth = result["berths"][0]
    assert berth["status"] == "partial_reference_check"
    assert [c["status"] for c in berth["checks"]] == ["within_listed_reference", "unavailable", "within_listed_reference"]
    assert result["admission"] == "not_assessed" and result["safe_draft_m"] is None
    assert berth["conditions_evaluated"] is False
    assert "METADATA_TIME_IS_NOT_DRAUGHT_MEASUREMENT_TIME" in result["warnings"]
    assert catalog.model_dump_json() == before


def test_reference_excess_is_displayed_without_actual_entry_rejection():
    data = catalog_data()
    data["observations"][0]["reported_draught_m"] = "6.6"
    result = screen_references(NavigationCatalog.model_validate(data), request(), now=NOW)
    assert result["berths"][0]["status"] == "published_reference_exceeded"
    assert result["admission"] == "not_assessed"


@pytest.mark.parametrize("mutation,reason", [
    ("old_record", "AIS_RECORD_STALE"), ("future_record", "AIS_RECORD_TIME_IN_FUTURE"),
    ("old_capture", "AIS_CAPTURE_STALE"), ("future_capture", "AIS_CAPTURE_TIME_IN_FUTURE"),
    ("no_time", "AIS_RECORD_TIME_UNKNOWN"), ("duplicate", "AIS_IDENTITY_AMBIGUOUS"),
    ("absent", "AIS_OBSERVATION_UNAVAILABLE"),
])
def test_invalid_time_or_identity_prevents_numeric_screening(mutation, reason):
    data = catalog_data()
    obs = data["observations"][0]
    if mutation == "old_record":
        obs["metadata_received_at"] = (NOW - timedelta(hours=24, seconds=1)).isoformat()
    elif mutation == "future_record":
        obs["metadata_received_at"] = (NOW + timedelta(seconds=1)).isoformat()
    elif mutation == "no_time":
        obs["metadata_received_at"] = None
    elif mutation in ("old_capture", "future_capture"):
        data["sources"][0]["retrieved_at"] = (NOW + timedelta(seconds=1) if mutation == "future_capture"
                                              else NOW - timedelta(hours=25)).isoformat()
    elif mutation == "duplicate":
        data["observations"].append(deepcopy(obs))
    elif mutation == "absent":
        data["observations"] = []
    result = screen_references(NavigationCatalog.model_validate(data), request(), now=NOW)
    assert reason in result["ship_issues"]
    assert result["berths"][0]["status"] == "insufficient_data"


def test_stale_berth_capture_and_future_catalog_do_not_remain_usable():
    data = catalog_data()
    data["sources"][2]["retrieved_at"] = (NOW - timedelta(days=31)).isoformat()
    result = screen_references(NavigationCatalog.model_validate(data), request(), now=NOW)
    assert "PORT_CAPTURE_STALE" in result["berths"][0]["checks"][0]["reasons"]
    data = catalog_data()
    data["generated_at"] = (NOW + timedelta(seconds=1)).isoformat()
    result = screen_references(NavigationCatalog.model_validate(data), request(), now=NOW)
    assert "CATALOG_TIME_IN_FUTURE" in result["ship_issues"]


def test_catalog_cannot_change_source_limits_or_promote_a_lower_bound_to_exact():
    data = catalog_data()
    data["ports"][0]["berths"][0]["max_loa_m"] = "999"
    with pytest.raises(ValidationError):
        NavigationCatalog.model_validate(data)
    data = catalog_data()
    data["observations"][0]["draught_lower_bound_m"] = "25.5"
    with pytest.raises(ValidationError):
        NavigationCatalog.model_validate(data)


def test_unknown_port_or_berth_cannot_fall_back_to_another_location():
    cat = NavigationCatalog.model_validate(catalog_data())
    for body in (NavigationScreenRequest(vessel_id=VESSEL, port_id="WPI:999"), request(berth_id=PORT + ":BERTH:999")):
        with pytest.raises(HTTPException) as exc:
            screen_references(cat, body, now=NOW)
        assert exc.value.status_code == 404


def test_request_cannot_override_sql_catalog_scope_or_freshness_clock():
    for extra in ({"now": NOW.isoformat()}, {"catalog_path": "file"}, {"sql": "DROP"}):
        with pytest.raises(ValidationError):
            NavigationScreenRequest(vessel_id=VESSEL, port_id=PORT, **extra)
    with pytest.raises(ValidationError):
        request(berth_id="WPI:999:BERTH:1")


def mock_engine():
    engine = MagicMock()
    engine.dialect.name = "postgresql"
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.mappings.return_value.one_or_none.return_value = {
        "vessel_id": VESSEL, "imo_number": IMO, "data_origin": "REAL"}
    return engine, connection


def test_database_lookup_is_read_only_bounded_and_parameterized_and_requires_existing_real_identity():
    engine, connection = mock_engine()
    catalog = NavigationCatalog.model_validate(catalog_data())
    result = screen_from_database(engine, request(), catalog, "a" * 64)
    assert result["vessel_id"] == VESSEL
    calls = connection.execute.call_args_list
    assert "READ ONLY" in str(calls[0].args[0]) and "statement_timeout='5s'" in str(calls[1].args[0])
    assert ":vessel_id" in str(calls[2].args[0]) and calls[2].args[1] == {"vessel_id": VESSEL}
    connection.execute.return_value.mappings.return_value.one_or_none.return_value = None
    with pytest.raises(HTTPException) as exc:
        screen_from_database(engine, request(), catalog, "a" * 64)
    assert exc.value.status_code == 404


def test_load_errors_do_not_disclose_server_paths_or_raw_data(tmp_path):
    with pytest.raises(HTTPException) as exc:
        load_catalog(tmp_path / "private-nonexistent-file")
    assert exc.value.status_code == 503 and "private" not in exc.value.detail
    p = tmp_path / "invalid.json"
    p.write_text('{"secret":"do-not-echo"}', encoding="utf-8")
    with pytest.raises(HTTPException) as exc:
        load_catalog(p)
    assert "do-not-echo" not in exc.value.detail


def test_api_auth_runs_before_loading_and_screening_and_legacy_routes_survive(monkeypatch, tmp_path):
    engine, connection = mock_engine()
    monkeypatch.setattr(api, "create_engine_for", lambda _url: engine)
    monkeypatch.delenv("MARITIME_PORT_REFERENCES_PATH", raising=False)
    app = api.create_app("postgresql://test.invalid/test", TOKEN)
    client = TestClient(app)
    body = {"vessel_id": VESSEL, "port_id": PORT}
    assert client.get('/api/maritime-data/port-reference-catalog').status_code == 401
    assert client.post('/api/maritime-data/port-reference-screen', json=body).status_code == 401
    assert client.post('/api/maritime-data/port-reference-screen', json=body,
                       headers={"Authorization": "Bearer wrong"}).status_code == 403
    connection.execute.assert_not_called()
    headers = {"Authorization": "Bearer " + TOKEN}
    assert client.get('/api/maritime-data/port-reference-catalog', headers=headers).status_code == 503
    p = tmp_path / "catalog.json"
    p.write_text(NavigationCatalog.model_validate(catalog_data()).model_dump_json(), encoding="utf-8")
    monkeypatch.setenv("MARITIME_PORT_REFERENCES_PATH", str(p))
    catalog = client.get('/api/maritime-data/port-reference-catalog', headers=headers)
    assert catalog.status_code == 200 and "observations" not in catalog.json()
    result = client.post('/api/maritime-data/port-reference-screen', json=body, headers=headers)
    assert result.status_code == 200 and result.json()["admission"] == "not_assessed"
    assert client.get('/api/maritime-data/factors', headers=headers).status_code == 200
    assert client.post('/api/maritime-data/port-reference-screen', json={**body, "now": NOW.isoformat()}, headers=headers).status_code == 422
    # File changes are checked on the next request; stale memory snapshots are not reused.
    p.write_text('{}', encoding="utf-8")
    assert client.get('/api/maritime-data/port-reference-catalog', headers=headers).status_code == 503


def prepared_sources(tmp_path):
    import csv
    raw = tmp_path / "raw"
    raw.mkdir()
    data = {
        "ais": json.dumps([ais()]).encode(),
        "schema": json.dumps({"components": {"schemas": {"VesselMetadataJsonV1": {"properties": {
            "draught": {"description": "Maximum present static draught in 1/10m"},
            "timestamp": {"description": "Record timestamp in milliseconds"},
        }}}}}).encode(),
        "berths": b'<h5>Berth 510</h5><p>Max. LOA: 100 metres</p><p>Max. draught: 6.5 metres</p>',
    }
    manifest = {}
    for key, payload in data.items():
        url, filename = URLS[key]
        (raw / filename).write_bytes(payload)
        manifest[key] = {"id": key, "url": url, "filename": filename, "sha256": digest(payload),
                         "status": "captured", "retrieved_at": NOW.isoformat()}
    (raw / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    files = {}
    for name, rows in {
        "vessels": [{"vessel_id": VESSEL, "imo_number": IMO, "data_origin": "REAL"}],
        "wpi": [{"World Port Index Number": "24020", "Main Port Name": "Goteborg", "Country Code": "Sweden",
                 "UN/LOCODE": "SE GOT", "Latitude": "57.7", "Longitude": "11.9",
                 **{v: "0.0" for v in PORT_NUMBERS.values()}}],
    }.items():
        path = tmp_path / (name + ".csv")
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        files[name] = path
    return raw, files, manifest


def test_offline_prepare_never_uses_network_and_preserves_sources_and_results(monkeypatch, tmp_path):
    raw, files, _manifest = prepared_sources(tmp_path)
    monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_kw: pytest.fail("Network used during prepare"))
    original = {k: p.read_bytes() for k, p in files.items()}
    output = tmp_path / "catalog.json"
    summary = prepare(raw, files["vessels"], files["wpi"], output)
    catalog, _hash = load_catalog(output)
    assert summary["mrv_linked_ais_rows"] == 1 and len(catalog.ports[0].berths) == 1
    assert original == {k: p.read_bytes() for k, p in files.items()}
    # Reject tampered bytes before replacing the previous prepared result.
    previous = output.read_bytes()
    (raw / "ais.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        prepare(raw, files["vessels"], files["wpi"], output)
    assert output.read_bytes() == previous


def test_source_contract_changes_and_untrusted_manifest_paths_require_review(tmp_path):
    raw, files, manifest = prepared_sources(tmp_path)
    manifest["ais"]["filename"] = "../private.json"
    with pytest.raises(ValueError, match="manifest"):
        captured(raw, manifest, "ais")
    manifest = json.loads((raw / "manifest.json").read_text())
    payload = (raw / "schema.json").read_bytes().replace(b"1/10m", b"different units")
    (raw / "schema.json").write_bytes(payload)
    manifest["schema"]["sha256"] = digest(payload)
    (raw / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="units changed"):
        prepare(raw, files["vessels"], files["wpi"], tmp_path / "never-created.json")
