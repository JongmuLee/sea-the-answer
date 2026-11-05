"""Explicit public-source capture and offline AIS/berth snapshot preparation."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import urllib.request

from backend.tools.port_reference import valid_imo
from scripts.prepare_port_references import normalize_ports, read_csv, PORT_NUMBERS

URLS = {
    "ais": ("https://meri.digitraffic.fi/api/ais/v1/vessels", "ais.json"),
    "schema": ("https://meri.digitraffic.fi/swagger/openapi.json", "schema.json"),
    "berths": ("https://www.portofgothenburg.com/maritime/berth-specifications/?epslanguage=en", "berths.html"),
    "operator": ("https://www.seaway7.com/vessels/hawk/", "operator.html"),
    "helsinki_berths": ("https://www.portofhelsinki.fi/en/professionals/information-for-port-users/instructions-for-cruise-ships/cruise-berth-details/", "helsinki-berths.html"),
}
LIMIT = 16 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def dump(path, payload):
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode()
    atomic_write(path, data)


def atomic_write(path, data):
    # Readers see either the previous complete snapshot or the next complete one.
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix="." + path.name + ".", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)  # Preserve existing permissions; new public snapshots are container-readable.
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def fetch(directory, *, include_helsinki=False):
    """Only this offline command can use the network. Runtime never calls it."""
    directory.mkdir(parents=True, exist_ok=False)
    manifest = {}
    for key, (url, filename) in URLS.items():
        if key == "helsinki_berths" and not include_helsinki:
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "SEA-THE-ANSWER-capstone/1.0",
                "Digitraffic-User": "SEA-THE-ANSWER/capstone-research", "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=25) as response:
                data = response.read(LIMIT + 1)
                if len(data) > LIMIT:
                    raise ValueError("Compressed source exceeds budget")
                if response.headers.get("Content-Encoding") == "gzip":
                    with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
                        data = stream.read(LIMIT + 1)
                if len(data) > LIMIT:
                    raise ValueError("Source exceeds budget")
                (directory / filename).write_bytes(data)
                manifest[key] = {"id": key, "url": url, "filename": filename, "sha256": digest(data),
                    "retrieved_at": datetime.now(timezone.utc).isoformat(), "effective_at": None,
                    "status": "captured"}
        except (OSError, ValueError) as exc:
            manifest[key] = {"id": key, "url": url, "status": "failed", "reason": str(exc)}
        dump(directory / "manifest.json", manifest)
    required = ("ais", "schema", "berths", "helsinki_berths") if include_helsinki else ("ais", "schema", "berths")
    if any(manifest[k]["status"] != "captured" for k in required):
        raise ValueError("Required source missing; see capture manifest, no catalog was prepared")
    return manifest


def captured(directory, manifest, key):
    item = manifest[key]
    if item.get("status") != "captured" or item.get("url") != URLS[key][0] or item.get("filename") != URLS[key][1]:
        raise ValueError("Source manifest does not match the supported primary source")
    data = (directory / URLS[key][1]).read_bytes()
    if len(data) > LIMIT or digest(data) != item.get("sha256"):
        raise ValueError("Source snapshot size/hash mismatch: " + key)
    return data


def integer(value, low, high):
    return value if type(value) is int and low <= value <= high else None


def ais_observations(rows, known_imos):
    if not isinstance(rows, list) or len(rows) > 50000:
        raise ValueError("Unexpected AIS array")
    imos, mmsis = Counter(), defaultdict(set)
    for row in rows:
        imo = str(row.get("imo", ""))
        if valid_imo(imo):
            imos[imo] += 1
            if integer(row.get("mmsi"), 100000000, 999999999):
                mmsis[row["mmsi"]].add(imo)
    observations = []
    for i, row in enumerate(rows):
        imo = str(row.get("imo", ""))
        if not valid_imo(imo) or imo not in known_imos:
            continue
        issues = []
        mmsi = integer(row.get("mmsi"), 100000000, 999999999)
        if mmsi is None or len(mmsis[mmsi]) > 1 or imos[imo] > 1:
            issues.append("AIS_IDENTITY_AMBIGUOUS")
        timestamp = integer(row.get("timestamp"), 946684800000, 4102444800000)
        received = datetime.fromtimestamp(timestamp / 1000, timezone.utc).isoformat() if timestamp else None
        if received is None:
            issues.append("AIS_METADATA_TIME_UNKNOWN")
        values = {}
        for name, letters, maximum in (("length", "AB", 510), ("beam", "CD", 62)):
            offsets = [integer(row.get("referencePoint" + a), 1, maximum) for a in letters]
            values["reported_" + name + "_m"] = str(sum(offsets)) if all(n is not None for n in offsets) else None
            if values["reported_" + name + "_m"] is None:
                issues.append("AIS_" + name.upper() + "_OFFSET_UNKNOWN_OR_BOUNDARY")
        draught = integer(row.get("draught"), 1, 254)
        # String arithmetic preserves tenths exactly, unlike float division.
        values["reported_draught_m"] = f"{draught // 10}.{draught % 10}" if draught is not None else None
        if draught is None:
            issues.append("AIS_DRAUGHT_LOWER_BOUND" if row.get("draught") == 255 else "AIS_DRAUGHT_UNAVAILABLE")
        observations.append({"vessel_id": "REAL:IMO:" + imo, "imo_number": imo,
            "mmsi": str(mmsi) if mmsi else None, "name": str(row.get("name", ""))[:80],
            "metadata_received_at": received, **values,
            "draught_lower_bound_m": "25.5" if row.get("draught") == 255 else None,
            "source_ref": {"source_id": "ais", "locator": "/" + str(i)},
            "issues": issues})
    return observations


class BerthSections(HTMLParser):
    """Read semantic heading blocks without executing scripts or guessing table spans."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sections = []
        self.heading_tag = None
        self.heading_text = []
        self.current = None
        self.ignored = 0
        self.context = {}

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.ignored += 1
        if self.ignored:
            return
        if re.fullmatch(r"h[1-6]", tag):
            if self.current and int(tag[1]) <= 5:
                self.sections.append(self.current)
                self.current = None
            self.heading_tag, self.heading_text = tag, []
        if tag in ("br", "p", "li", "div") and self.current:
            self.current[1].append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.ignored = max(0, self.ignored - 1)
        if self.ignored:
            return
        if tag == self.heading_tag:
            title = " ".join("".join(self.heading_text).split())
            if tag == "h5" and re.fullmatch(r"Berth\s+[0-9A-Z -]+", title, re.I):
                context = " / ".join(self.context[k] for k in sorted(self.context))
                self.current = (title, ["Source section: " + context + "\n"] if context else [])
            elif self.current:
                self.current[1].extend(["\n", title, "\n"])
            if int(tag[1]) <= 4:
                level = int(tag[1])
                self.context = {k: v for k, v in self.context.items() if k < level}
                self.context[level] = title
            self.heading_tag = None
        if tag in ("p", "li", "div") and self.current:
            self.current[1].append("\n")

    def handle_data(self, data):
        if not self.ignored:
            if self.heading_tag:
                self.heading_text.append(data)
            elif self.current:
                self.current[1].append(data)

    def finish(self):
        if self.current:
            self.sections.append(self.current)
            self.current = None
        return [(name, "\n".join(line.strip() for line in "".join(text).replace("\xa0", " ").splitlines()
                                if line.strip())) for name, text in self.sections]


def listed_number(value):
    # Do not flatten ranges, alternatives, density qualifications, or multiple numbers.
    match = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*(?:metres|meters|m)\s*[.]?", value, re.I)
    return match[1].replace(",", ".") if match and float(match[1].replace(",", ".")) > 0 else None


def berth_references(html, wpi_id):
    parser = BerthSections()
    parser.feed(html)
    grouped = defaultdict(list)
    for ordinal, (heading, body) in enumerate(parser.finish()):
        if len(body) > 6000:
            raise ValueError("Berth condition block exceeded budget; do not truncate restrictions")
        label = re.sub(r"^Berth\s*", "", heading, flags=re.I).replace(" ", "").upper()
        values = {}
        for key, pattern in (("max_loa_m", r"(?:Max\.?\s*LOA|Maximum length of vessel)"),
                             ("max_draught_m", r"Max\.?\s*(?:draught|draft)")):
            found = re.findall(r"(?im)^" + pattern + r"\s*:\s*([^\n]+)", body)
            values[key] = listed_number(found[0].strip()) if len(found) == 1 else None
        grouped[label].append({"heading": heading, "text": body, **values,
            "source_ref": {"source_id": "berths", "locator": heading + "; block=" + str(ordinal)}})
    if not grouped:
        raise ValueError("No berth headings found; source structure may have changed")
    berths = []
    for label, variants in grouped.items():
        issues = ["DUPLICATE_BERTH_VARIANTS_REQUIRE_REVIEW"] if len(variants) > 1 else []
        berth = {"berth_id": wpi_id + ":BERTH:" + label, "label": label, "variants": variants,
                 "source_ref": variants[0]["source_ref"], "issues": issues,
                 "conditions": [v["text"] for v in variants]}
        for field in ("max_loa_m", "max_draught_m"):
            berth[field] = variants[0][field] if len(variants) == 1 else None
            if berth[field] is None:
                berth["issues"].append(field.upper() + "_UNKNOWN_OR_CONDITIONAL")
        berth["max_beam_m"] = None  # ramp width / quay width is NOT vessel beam permission
        berths.append(berth)
    return berths


def prepare(raw, vessels_path, wpi_path, output):
    from backend.tools.navigation_references import NavigationCatalog
    manifest = json.loads((raw / "manifest.json").read_text(encoding="utf-8"))
    ais = json.loads(captured(raw, manifest, "ais"))
    schema_bytes = captured(raw, manifest, "schema")
    schema = json.loads(schema_bytes)["components"]["schemas"]["VesselMetadataJsonV1"]["properties"]
    if "1/10m" not in schema["draught"]["description"] or "milliseconds" not in schema["timestamp"]["description"]:
        raise ValueError("Upstream units changed; review the official schema before processing")
    html = captured(raw, manifest, "berths").decode("utf-8")
    vessels_bytes, wpi_bytes = vessels_path.read_bytes(), wpi_path.read_bytes()
    vessels = read_csv(vessels_path, ["vessel_id", "imo_number", "data_origin"])
    ids = set()
    for v in vessels:
        if v["data_origin"] == "REAL":
            imo = v["imo_number"]
            if not valid_imo(imo) or v["vessel_id"] != "REAL:IMO:" + imo or imo in ids:
                raise ValueError("Invalid or duplicate prepared real IMO identity")
            ids.add(imo)
    ports = normalize_ports(read_csv(wpi_path, ["World Port Index Number", "Main Port Name", "Country Code",
        "UN/LOCODE", "Latitude", "Longitude", *PORT_NUMBERS.values()]), {})
    def reviewed_port(code):
        matched = [p for p in ports if p["unlocode"] == code]
        if len(matched) != 1 or "WPI_ID_NON_UNIQUE_REQUIRES_REVIEW" in matched[0]["issues"]:
            raise ValueError("The reviewed " + code + " to WPI mapping is no longer unique")
        return matched[0]["port_id"]
    gothenburg = reviewed_port("SEGOT")
    catalog_ports = [{"port_id": gothenburg, "unlocode": "SEGOT", "name": "Gothenburg",
        "source_id": "berths", "reference_scope": "Selected published berths; terminal and cargo suitability not assessed",
        "berths": berth_references(html, gothenburg)}]
    source_keys = ["ais", "schema", "berths"]
    if "helsinki_berths" in manifest:
        from scripts.prepare_helsinki_references import helsinki_berths
        helsinki = reviewed_port("FIHEL")
        helsinki_html = captured(raw, manifest, "helsinki_berths").decode("utf-8")
        catalog_ports.append({"port_id": helsinki, "unlocode": "FIHEL", "name": "Helsinki",
            "source_id": "helsinki_berths", "reference_scope": "Cruise berths only; vessel/service suitability not assessed; survey year retained in conditions",
            "berths": helsinki_berths(helsinki_html, helsinki)})
        source_keys.append("helsinki_berths")
    observations = ais_observations(ais, ids)
    sources = []
    for key in source_keys:
        m = manifest[key]
        sources.append({"id": key, "url": m["url"], "sha256": m["sha256"],
            "retrieved_at": m["retrieved_at"], "effective_at": None,
            "attribution": "Fintraffic / digitraffic.fi; CC BY 4.0; IMO-filtered and normalized" if key in ("ais", "schema")
                else ("Port of Helsinki Ltd; cruise berth reference facts, conditions and survey year retained" if key == "helsinki_berths"
                      else "Gothenburg Port Authority; extracted berth reference facts, conditions retained"),
            "license_url": "https://creativecommons.org/licenses/by/4.0/" if key in ("ais", "schema") else None})
    catalog = NavigationCatalog.model_validate({
        "version": "NAVIGATION-REFERENCES-1.1", "generated_at": datetime.now(timezone.utc).isoformat(),
        "mrv_vessels_sha256": digest(vessels_bytes), "wpi_sha256": digest(wpi_bytes), "sources": sources,
        "observations": observations, "ports": catalog_ports,
    })
    payload = catalog.model_dump(mode="json")
    output.parent.mkdir(parents=True, exist_ok=True)
    dump(output, payload)
    summary = {"ais_raw_rows": len(ais), "mrv_population": len(ids),
        "mrv_linked_ais_rows": len(observations), "unique_imo": len({r["imo_number"] for r in observations}),
        "reported_draught_exact_rows": sum(r["reported_draught_m"] is not None for r in observations),
        "reported_length_and_beam_rows": sum(r["reported_length_m"] is not None and r["reported_beam_m"] is not None
                                              for r in observations),
        "identity_ambiguous_rows": sum("AIS_IDENTITY_AMBIGUOUS" in r["issues"] for r in observations),
        "ports": len(catalog.ports), "berth_groups": sum(len(p.berths) for p in catalog.ports),
        "berths_with_simple_loa_limit": sum(b.max_loa_m is not None for p in catalog.ports for b in p.berths),
        "berths_with_simple_draught_limit": sum(b.max_draught_m is not None for p in catalog.ports for b in p.berths),
        "duplicate_berth_groups": sum(len(b.variants) > 1 for p in catalog.ports for b in p.berths),
        "port_details": [{"port_id": p.port_id, "unlocode": p.unlocode, "name": p.name,
                          "berth_groups": len(p.berths), "reference_scope": p.reference_scope} for p in catalog.ports],
        "catalog_sha256": digest(output.read_bytes()), "admission": "not_assessed"}
    dump(output.with_suffix(".summary.json"), summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    capture = sub.add_parser("fetch")
    capture.add_argument("--raw-dir", type=Path, required=True)
    capture.add_argument("--include-helsinki", action="store_true", help="Include the reviewed Helsinki cruise berth source")
    build = sub.add_parser("prepare")
    for name in ("raw-dir", "vessels", "wpi", "output"):
        build.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    result = fetch(args.raw_dir, include_helsinki=args.include_helsinki) if args.command == "fetch" else prepare(args.raw_dir, args.vessels, args.wpi, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
