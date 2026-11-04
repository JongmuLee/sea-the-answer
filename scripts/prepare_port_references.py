"""Read-only linkage audit and offline port-reference comparison pilot.

Existing datasets are never changed. Network access is explicit (--fetch-wikidata).
This creates research files, not runtime DB rows, entry permissions or live AIS.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request

from backend.tools.port_reference import compare_port_reference, dimension, positive, valid_imo

MARINE_SOURCE = "https://github.com/rich-iannone/MarineVessels/tree/935bf969301ad8916dce1d615ffd95df6d2073c2"
WPI_SOURCE = "https://msi.nga.mil/Publications/WPI"
WPI_FIELDS = "https://msi.nga.mil/api/publications/download?key=16920959/SFH00000/WPI_Explanation_of_Data_Fields.pdf&type=view"
PROPERTY_NAMES = {"P2043": "length", "P2049": "beam", "P2262": "historical_draft"}
PORT_NUMBERS = {
    "max_length_m": "Maximum Vessel Length (m)",
    "max_beam_m": "Maximum Vessel Beam (m)",
    "max_draft_m": "Maximum Vessel Draft (m)",
    "channel_depth_m": "Channel Depth (m)",
    "cargo_pier_depth_m": "Cargo Pier Depth (m)",
    "oil_terminal_depth_m": "Oil Terminal Depth (m)",
}


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_csv(path, required):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not set(required) <= set(reader.fieldnames or []):
            raise ValueError("Missing required columns: " + path.name)
        rows = list(reader)
        if any(None in row or any(v is None for v in row.values()) for row in rows):
            raise ValueError("Malformed CSV row: " + path.name)
        return rows


def source(path, url):
    return {"filename": path.name, "sha256": sha256(path), "url": url, "effective_at": None}


def marine_links(vessels, marine_rows, marine_source):
    indexed = defaultdict(list)
    for number, row in enumerate(marine_rows, 2):
        imo = row["IMO"].strip()
        if valid_imo(imo):
            indexed[imo].append({"source_row": number, "raw": row})
    linked = []
    seen = set()
    for row in vessels:
        if row["data_origin"] != "REAL":
            continue
        imo = row["imo_number"]
        if not valid_imo(imo) or row["vessel_id"] != "REAL:IMO:" + imo or imo in seen:
            raise ValueError("Invalid or duplicated real vessel identity")
        seen.add(imo)
        matches = indexed[imo]
        status = "matched" if len(matches) == 1 else "ambiguous" if matches else "unmatched"
        linked.append({
            "vessel_id": row["vessel_id"], "imo_number": imo, "marine_status": status,
            "marine_source": marine_source, "marine_rows": matches,
            "marine_unit_status": "NOT_DOCUMENTED_DO_NOT_USE_AS_METRES",
            "current_draft_m": None,
        })
    return sorted(linked, key=lambda r: r["imo_number"])


def port_id(value):
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError("Invalid WPI number") from None
    if not number.is_finite() or number <= 0 or number != number.to_integral_value():
        raise ValueError("Invalid WPI number")
    return "WPI:" + str(int(number))


def normalize_ports(rows, metadata):
    ports = []
    for number, raw in enumerate(rows, 2):
        key = port_id(raw["World Port Index Number"])
        code = re.sub(r"\s+", "", raw["UN/LOCODE"].strip().upper())
        # Format only: this does not assert UNECE registration or uniqueness.
        code_valid = bool(re.fullmatch(r"[A-Z]{2}[A-Z0-9]{3}", code))
        issues = []
        item = {"port_id": key, "name": raw["Main Port Name"].strip(),
                "country": raw["Country Code"].strip(), "unlocode": code if code_valid else None,
                "source": {**metadata, "source_row": number}, "raw": raw,
                "source_columns": PORT_NUMBERS}
        for field, column in PORT_NUMBERS.items():
            value = positive(raw[column])
            item[field] = str(value) if value is not None else None
            if value is None:
                issues.append(field + ":MISSING_ZERO_OR_INVALID_NOT_UNLIMITED")
        for axis, maximum in (("latitude", 90), ("longitude", 180)):
            try:
                coordinate = Decimal(raw[axis.title()])
                if not coordinate.is_finite() or not -maximum <= coordinate <= maximum:
                    raise InvalidOperation
                item[axis] = str(coordinate)
            except (InvalidOperation, ValueError):
                item[axis] = None
                issues.append(axis + ":INVALID_OR_MISSING")
        item["issues"] = issues
        ports.append(item)
    counts = Counter(p["unlocode"] for p in ports if p["unlocode"])
    identity_counts = Counter(p["port_id"] for p in ports)
    for item in ports:
        if identity_counts[item["port_id"]] > 1:
            item["issues"].append("WPI_ID_NON_UNIQUE_REQUIRES_REVIEW")
        if item["unlocode"] and counts[item["unlocode"]] > 1:
            item["issues"].append("UNLOCODE_NON_UNIQUE_USE_WPI_ID")
    return sorted(ports, key=lambda p: int(p["port_id"].split(":")[1]))


def query_for(imos):
    if not imos or len(imos) > 100 or any(not valid_imo(i) for i in imos):
        raise ValueError("Select 1..100 checksum-valid IMO numbers")
    values = " ".join('"' + i + '"' for i in sorted(set(imos)))
    return """SELECT ?ship ?imo ?property ?statement ?rank ?value ?unit WHERE {
  VALUES ?imo { """ + values + """ }
  ?ship wdt:P458 ?imo.
  VALUES (?property ?predicate) {
    (wd:P2043 p:P2043) (wd:P2049 p:P2049) (wd:P2262 p:P2262)
  }
  ?ship ?predicate ?statement.
  ?statement wikibase:rank ?rank.
  FILTER(?rank != wikibase:DeprecatedRank)
  ?property wikibase:statementValue ?psv.
  ?statement ?psv ?quantity.
  ?quantity wikibase:quantityAmount ?value; wikibase:quantityUnit ?unit.
} ORDER BY ?imo ?property ?statement ?value ?unit
"""


def fetch_wikidata(imos, directory):
    query = query_for(imos)
    url = "https://query.wikidata.org/sparql?" + urllib.parse.urlencode({"query": query, "format": "json"})
    req = urllib.request.Request(url, headers={"User-Agent": "SeaTheAnswer-CapstoneResearch/1.0",
                                               "Accept": "application/sparql-results+json"})
    with urllib.request.urlopen(req, timeout=30) as response:
        payload = response.read(8 * 1024 * 1024 + 1)
        if len(payload) > 8 * 1024 * 1024:
            raise ValueError("Wikidata response exceeded 8 MiB")
    data = json.loads(payload)
    if "results" not in data or not isinstance(data["results"].get("bindings"), list):
        raise ValueError("Invalid SPARQL response")
    path = directory / "wikidata-dimensions.json"
    path.write_bytes(payload)
    (directory / "wikidata-dimensions.rq").write_bytes(query.encode("utf-8"))
    meta = {**source(path, "https://query.wikidata.org/"),
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
            "selected_imos": sorted(set(imos)), "selection": "explicit_nonrandom_pilot_not_population_coverage"}
    write_json(directory / "wikidata-manifest.json", meta)
    return data, meta


def add_wikidata(ships, data, metadata):
    allowed = set(metadata["selected_imos"])
    if any(not valid_imo(i) for i in allowed):
        raise ValueError("Invalid IMO in snapshot manifest")
    facts = defaultdict(lambda: defaultdict(list))
    entities = defaultdict(set)
    for row in data["results"]["bindings"]:
        imo = row["imo"]["value"]
        if imo not in allowed:
            raise ValueError("Unexpected IMO outside the requested snapshot")
        prop = row["property"]["value"].rsplit("/", 1)[-1]
        if prop not in PROPERTY_NAMES:
            raise ValueError("Unexpected dimension property")
        entities[imo].add(row["ship"]["value"])
        facts[imo][PROPERTY_NAMES[prop]].append({
            "entity": row["ship"]["value"], "statement": row["statement"]["value"],
            "property": prop, "value": row["value"]["value"], "unit": row["unit"]["value"],
            "rank": row["rank"]["value"],
        })
    for ship in ships:
        imo = ship["imo_number"]
        ship["wikidata_source"] = metadata if imo in allowed else None
        ship["wikidata_sampled"] = imo in allowed
        ship["wikidata_dimensions"] = {name: dimension(facts[imo][name]) for name in PROPERTY_NAMES.values()}
        if len(entities[imo]) > 1:
            for value in ship["wikidata_dimensions"].values():
                value["value_m"] = None
                value["issues"].append("MULTIPLE_ENTITIES_FOR_IMO")


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build(args):
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    rawdir = directory / "raw"
    rawdir.mkdir(exist_ok=True)
    vessels = read_csv(args.vessels, ["vessel_id", "imo_number", "data_origin"])
    marine = read_csv(args.marine, ["IMO", "length", "beam", "ship_draught"])
    wpi = read_csv(args.wpi, ["World Port Index Number", "Main Port Name", "Country Code", "UN/LOCODE",
                              "Latitude", "Longitude", *PORT_NUMBERS.values()])
    ship_source = source(args.marine, MARINE_SOURCE)
    ships = marine_links(vessels, marine, ship_source)
    ports = normalize_ports(wpi, source(args.wpi, WPI_SOURCE))
    if args.fetch_wikidata:
        selection = [s["imo_number"] for s in ships if s["marine_status"] == "matched"]
        if args.discovery_json:
            discovered = json.loads(args.discovery_json.read_text(encoding="utf-8"))
            seen = {r["imo"]["value"] for r in discovered["results"]["bindings"]}
            selection = [i for i in selection if i in seen]
        selection = selection[:args.sample_size]
        snapshot, metadata = fetch_wikidata(selection, rawdir)
        if args.discovery_json:
            metadata["discovery"] = {
                "filename": args.discovery_json.name, "sha256": sha256(args.discovery_json),
                "purpose": "nonrandom_discovery_only_not_coverage_measurement",
                "rows": len(discovered["results"]["bindings"]),
            }
            write_json(rawdir / "wikidata-manifest.json", metadata)
    else:
        snapshot = json.loads((rawdir / "wikidata-dimensions.json").read_text(encoding="utf-8"))
        metadata = json.loads((rawdir / "wikidata-manifest.json").read_text(encoding="utf-8"))
        if sha256(rawdir / "wikidata-dimensions.json") != metadata["sha256"]:
            raise ValueError("Wikidata snapshot hash mismatch")
        query_text = (rawdir / "wikidata-dimensions.rq").read_text(encoding="utf-8")
        if hashlib.sha256(query_text.encode("utf-8")).hexdigest() != metadata["query_sha256"]:
            raise ValueError("Wikidata query hash mismatch")
    add_wikidata(ships, snapshot, metadata)
    selected = [s for s in ships if s["wikidata_sampled"]]
    complete = [s for s in selected if all(s["wikidata_dimensions"][a]["value_m"] is not None
                                                   for a in ("length", "beam"))]
    marine_matched = [s for s in ships if s["marine_status"] == "matched"]
    summary = {
        "version": "PORT-LINKAGE-PILOT-0.1", "generated_at": datetime.now(timezone.utc).isoformat(),
        "inputs": {"vessels": source(args.vessels, "existing_prepared_MRV_identifiers"),
                   "marine": ship_source, "wpi": source(args.wpi, WPI_SOURCE), "wikidata": metadata},
        "real_mrv_vessels": len(ships), "marine_rows": len(marine),
        "marine_exact_unique_matches": len(marine_matched),
        "marine_match_percent": round(100 * len(marine_matched) / len(ships), 2) if ships else None,
        "marine_positive_raw_field_counts_not_verified_units": {
            field: sum(positive(s["marine_rows"][0]["raw"][field]) is not None for s in marine_matched)
            for field in ("length", "beam", "ship_draught")},
        "marine_ambiguous_matches": sum(s["marine_status"] == "ambiguous" for s in ships),
        "wikidata_selected_imos": len(selected), "wikidata_length_and_beam_in_metres": len(complete),
        "ports": len(ports), "ports_with_both_length_and_beam_limits": sum(
            p["max_length_m"] is not None and p["max_beam_m"] is not None for p in ports),
        "port_positive_field_counts": {k: sum(p[k] is not None for p in ports) for k in PORT_NUMBERS},
        "ports_with_non_unique_unlocode": sum("UNLOCODE_NON_UNIQUE_USE_WPI_ID" in p["issues"] for p in ports),
        "ports_with_non_unique_wpi_id": sum("WPI_ID_NON_UNIQUE_REQUIRES_REVIEW" in p["issues"] for p in ports),
        "official_field_definitions": WPI_FIELDS,
        "limits": ["NONRANDOM_WIKIDATA_PILOT_NOT_GLOBAL_COVERAGE", "NO_CURRENT_DRAFT_OR_AIS",
                   "NO_SHIP_PORT_CALL_JOIN", "NO_CONFIRMED_ENTRY_DECISION", "NO_OPERATIONAL_DB_OR_API_CHANGE",
                   "SOURCE_EFFECTIVE_DATES_UNKNOWN", "MARINE_UNITS_NOT_DOCUMENTED"],
    }
    # A compact linked research dataset, retaining every real ID including unmatched ones.
    with (directory / "ship-references.jsonl").open("w", encoding="utf-8") as stream:
        for ship in ships:
            stream.write(json.dumps(ship, ensure_ascii=False) + "\n")
    write_json(directory / "port-references.json", ports)
    write_json(directory / "linkage-summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    for name in ("vessels", "marine", "wpi", "output"):
        prepare.add_argument("--" + name, type=Path, required=True)
    prepare.add_argument("--fetch-wikidata", action="store_true")
    prepare.add_argument("--sample-size", type=int, choices=range(1, 101), default=50, metavar="1..100")
    prepare.add_argument("--discovery-json", type=Path)
    compare = commands.add_parser("compare")
    compare.add_argument("--data-dir", type=Path, required=True)
    compare.add_argument("--imo", required=True)
    compare.add_argument("--port-id", required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        print(json.dumps(build(args), ensure_ascii=False, indent=2))
    else:
        ships = [json.loads(line) for line in (args.data_dir / "ship-references.jsonl").read_text(encoding="utf-8").splitlines()]
        ports = json.loads((args.data_dir / "port-references.json").read_text(encoding="utf-8"))
        candidates = [s for s in ships if s["imo_number"] == args.imo]
        destinations = [p for p in ports if p["port_id"] == args.port_id]
        if len(candidates) != 1 or len(destinations) != 1:
            parser.error("Exactly one known real IMO and WPI identity are required")
        print(json.dumps(compare_port_reference(candidates[0], destinations[0]), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
