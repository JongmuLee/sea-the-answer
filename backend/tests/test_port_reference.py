"""Prevent false joins and false entry permissions in the read-only port pilot."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from tools.port_reference import compare_port_reference, dimension, positive, valid_imo
from scripts.prepare_port_references import (
    PORT_NUMBERS, add_wikidata, build, marine_links, normalize_ports, port_id, query_for,
)

IMO = "7422881"
METRE = "http://www.wikidata.org/entity/Q11573"


def fact(value="100", unit=METRE):
    return {"value": value, "unit": unit, "statement": "source-claim"}


def ship(length="100", beam="20"):
    return {"vessel_id": "REAL:IMO:" + IMO, "imo_number": IMO,
            "wikidata_dimensions": {"length": dimension([fact(length)]),
                                    "beam": dimension([fact(beam)])}}


def port(length="100", beam="20"):
    return {"port_id": "WPI:60400", "name": "Test port", "max_length_m": length,
            "max_beam_m": beam, "issues": []}


@pytest.mark.parametrize("value", [None, True, False, "", "NA", "0", "-1", "NaN", "Infinity", "1e100"])
def test_invalid_dimensions_are_unknown_not_unlimited(value):
    assert positive(value) is None


@pytest.mark.parametrize("value", ["7422880", "IMO7422881", "7422881.0", "123", "SYN:A", "７４２２８８１"])
def test_imo_rejects_invalid_checksum_or_identity_format(value):
    assert not valid_imo(value)


def test_units_convert_without_guessing_and_conflicts_block():
    cm = "http://www.wikidata.org/entity/Q174728"
    assert dimension([fact("100"), fact("10000", cm)])["value_m"] == "100"
    assert dimension([fact("100"), fact("101")])["value_m"] is None
    assert "CONFLICTING_QUANTITIES" in dimension([fact("100"), fact("101")])["issues"]
    assert dimension([fact(unit="1")])["value_m"] is None
    assert dimension([])["value_m"] is None
    assert dimension([fact(), fact("bad")])["value_m"] is None


def test_equal_limits_are_only_reference_comparisons_not_entry_clearance():
    s, p = ship(), port()
    before = deepcopy((s, p))
    result = compare_port_reference(s, p)
    assert result["status"] == "reference_values_within_limits"
    assert result["admission"] == "not_assessed"
    assert result["safe_draft_m"] is None
    assert "CURRENT_DRAFT_AND_OBSERVATION_TIME" in result["required_before_admission"]
    assert (s, p) == before


def test_exceedance_does_not_authorize_or_reject_actual_entry():
    result = compare_port_reference(ship("100.01"), port())
    assert result["status"] == "reference_exceedance"
    assert result["admission"] == "not_assessed"


def test_unknown_port_limit_does_not_mean_no_limit_or_use_channel_depth():
    p = {**port(None), "channel_depth_m": "100", "tidal_range_m": "10"}
    result = compare_port_reference(ship(), p)
    assert result["status"] == "insufficient_reference_data"
    assert result["comparisons"][0]["status"] == "unavailable"
    assert result["safe_draft_m"] is None


def test_draft_and_marine_candidates_are_not_silently_used():
    s = ship()
    s["wikidata_dimensions"] = {}
    s["marine_rows"] = [{"raw": {"length": "100", "beam": "20", "ship_draught": "5"}}]
    result = compare_port_reference(s, port())
    assert result["status"] == "insufficient_reference_data"
    assert result["comparisons"][0]["ship_reference_m"] is None


@pytest.mark.parametrize("change", [
    {"vessel_id": "SYN:A"}, {"imo_number": "7422880"},
])
def test_comparison_rejects_fake_or_mismatched_ship(change):
    with pytest.raises(ValueError):
        compare_port_reference({**ship(), **change}, port())


def raw_port(**changes):
    return {"World Port Index Number": "60400.0", "Main Port Name": "Ulsan", "Country Code": "South Korea",
            "UN/LOCODE": "KR USN", "Latitude": "35.5", "Longitude": "129.3",
            **{v: "0.0" for v in PORT_NUMBERS.values()}, **changes}


def test_port_normalization_preserves_unknowns_coordinates_and_original_rows():
    raw = raw_port(**{"Maximum Vessel Length (m)": "210"})
    p = normalize_ports([raw], {"sha256": "a" * 64})[0]
    assert p["port_id"] == "WPI:60400" and p["unlocode"] == "KRUSN"
    assert p["max_length_m"] == "210" and p["max_beam_m"] is None
    assert p["raw"] == raw and p["source"]["source_row"] == 2
    invalid = normalize_ports([raw_port(Latitude="91", Longitude="NaN")], {})[0]
    assert invalid["latitude"] is None and invalid["longitude"] is None


def test_duplicate_wpi_and_unlocode_preserved_but_not_silently_resolved():
    ports = normalize_ports([raw_port(), raw_port(**{"Main Port Name": "Other"})], {})
    assert len(ports) == 2
    assert all("WPI_ID_NON_UNIQUE_REQUIRES_REVIEW" in p["issues"] for p in ports)
    assert all("UNLOCODE_NON_UNIQUE_USE_WPI_ID" in p["issues"] for p in ports)
    with pytest.raises(ValueError, match="Ambiguous"):
        compare_port_reference(ship(), ports[0])


@pytest.mark.parametrize("value", ["NaN", "Infinity", "0", "-1", "4.5"])
def test_wpi_id_cannot_be_rounded_or_nonfinite(value):
    with pytest.raises(ValueError):
        port_id(value)


def test_marine_link_is_exact_and_duplicates_do_not_pick_first():
    vessels = [{"vessel_id": "REAL:IMO:" + IMO, "imo_number": IMO, "data_origin": "REAL"},
               {"vessel_id": "SYN:A", "imo_number": IMO, "data_origin": "SYNTHETIC"}]
    raw = {"IMO": IMO, "name": "Old name"}
    result = marine_links(vessels, [raw, {**raw, "name": "Other name"}], {})
    assert len(result) == 1 and result[0]["marine_status"] == "ambiguous"
    assert len(result[0]["marine_rows"]) == 2
    assert result[0]["current_draft_m"] is None


def binding(entity="Q1", imo=IMO, value="100"):
    return {k: {"value": v} for k, v in {
        "imo": imo, "ship": "http://www.wikidata.org/entity/" + entity,
        "property": "http://www.wikidata.org/entity/P2043", "statement": entity + "-statement",
        "value": value, "unit": METRE, "rank": "http://wikiba.se/ontology#NormalRank",
    }.items()}


def test_multiple_entities_with_same_imo_block_automatic_dimension_selection():
    ships = [ship()]
    add_wikidata(ships, {"results": {"bindings": [binding(), binding("Q2")]}}, {"selected_imos": [IMO]})
    dims = ships[0]["wikidata_dimensions"]
    assert dims["length"]["value_m"] is None
    assert "MULTIPLE_ENTITIES_FOR_IMO" in dims["length"]["issues"]


def test_snapshot_cannot_introduce_other_ships_and_query_cannot_accept_sql_or_sparql():
    with pytest.raises(ValueError):
        add_wikidata([ship()], {"results": {"bindings": [binding(imo="9074729")]}}, {"selected_imos": [IMO]})
    with pytest.raises(ValueError):
        query_for(['" } ?s ?p ?o'])
    assert "DeprecatedRank" in query_for([IMO])
    assert "LIMIT" not in query_for([IMO])  # no silent truncation of selected-IMO facts


def prepared_args(tmp_path):
    import csv
    raw = tmp_path / "raw"
    raw.mkdir()
    data = json.dumps({"results": {"bindings": [binding()]}}).encode()
    (raw / "wikidata-dimensions.json").write_bytes(data)
    query = query_for([IMO])
    # Accept only newline changes between Windows and Linux query copies.
    (raw / "wikidata-dimensions.rq").write_bytes(query.replace("\n", "\r\n").encode())
    (raw / "wikidata-manifest.json").write_text(json.dumps({
        "sha256": hashlib.sha256(data).hexdigest(), "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
        "selected_imos": [IMO],
    }), encoding="utf-8")
    values = {
        "vessels": [{"vessel_id": "REAL:IMO:" + IMO, "imo_number": IMO, "data_origin": "REAL"}],
        "marine": [{"IMO": IMO, "length": "100", "beam": "20", "ship_draught": "5"}],
        "wpi": [raw_port()],
    }
    paths = {}
    for name, rows in values.items():
        path = tmp_path / (name + ".csv")
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        paths[name] = path
    return SimpleNamespace(**paths, output=tmp_path, fetch_wikidata=False)


def test_offline_prepare_checks_saved_snapshot_hash(tmp_path):
    args = prepared_args(tmp_path)
    (tmp_path / "raw/wikidata-dimensions.json").write_text('{"changed":true}', encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        build(args)


def test_offline_prepare_is_reproducible_with_crlf_query_and_rejects_query_changes(tmp_path):
    args = prepared_args(tmp_path)
    first = build(args)
    before = (tmp_path / "ship-references.jsonl").read_bytes()
    second = build(args)
    assert first["real_mrv_vessels"] == second["real_mrv_vessels"] == 1
    assert (tmp_path / "ship-references.jsonl").read_bytes() == before
    with (tmp_path / "raw/wikidata-dimensions.rq").open("a", encoding="utf-8") as stream:
        stream.write("# different query")
    with pytest.raises(ValueError, match="query hash mismatch"):
        build(args)
