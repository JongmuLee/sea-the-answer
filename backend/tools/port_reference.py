"""Compare published reference values; never authorize entry or derive safe draft."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re


VERSION = "PORT-REFERENCE-COMPARISON-0.1"
METRE_UNITS = {
    "http://www.wikidata.org/entity/Q11573": Decimal("1"),
    "http://www.wikidata.org/entity/Q174728": Decimal("0.01"),
    "http://www.wikidata.org/entity/Q174789": Decimal("0.001"),
    "http://www.wikidata.org/entity/Q3710": Decimal("0.3048"),
}


def positive(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and 0 < number < Decimal("1e8") else None


def valid_imo(value):
    """No name/fuzzy joins, decimal truncation, or masked-identifier recovery."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{7}", value):
        return False
    return sum(int(n) * w for n, w in zip(value[:6], range(7, 1, -1))) % 10 == int(value[-1])


def dimension(facts):
    """Keep every source fact; disagreement or unknown units prevents selection."""
    converted = []
    problems = []
    for fact in facts:
        number = positive(fact.get("value"))
        factor = METRE_UNITS.get(fact.get("unit"))
        if number is None:
            problems.append("INVALID_QUANTITY")
        elif factor is None:
            problems.append("UNSUPPORTED_OR_MISSING_UNIT")
        else:
            converted.append(number * factor)
    if len(set(converted)) > 1:
        problems.append("CONFLICTING_QUANTITIES")
    if not facts:
        problems.append("NO_QUANTITY")
    return {
        "value_m": str(converted[0]) if converted and not problems else None,
        "issues": sorted(set(problems)),
        "facts": facts,
    }


def compare_port_reference(ship, port):
    """A static source-value comparison, deliberately independent of LLM/DB/API."""
    imo = ship.get("imo_number")
    if not valid_imo(imo) or ship.get("vessel_id") != "REAL:IMO:" + imo:
        raise ValueError("A real, checksum-valid IMO-linked vessel is required")
    if not re.fullmatch(r"WPI:[0-9]+", str(port.get("port_id", ""))):
        raise ValueError("An explicit WPI port ID is required")
    if "WPI_ID_NON_UNIQUE_REQUIRES_REVIEW" in port.get("issues", []):
        raise ValueError("Ambiguous WPI identity requires review")
    comparisons = []
    for axis in ("length", "beam"):
        measured = ship.get("wikidata_dimensions", {}).get(axis, {})
        value = positive(measured.get("value_m")) if not measured.get("issues") else None
        limit = positive(port.get("max_" + axis + "_m"))
        reasons = []
        if value is None:
            reasons.append("SHIP_REFERENCE_" + axis.upper() + "_UNAVAILABLE")
        if limit is None:
            reasons.append("PORT_" + axis.upper() + "_LIMIT_UNKNOWN")
        state = "unavailable"
        if not reasons:
            state = "exceeds_published_reference" if value > limit else "within_published_reference"
        comparisons.append({
            "axis": axis, "ship_reference_m": str(value) if value is not None else None,
            "port_published_limit_m": str(limit) if limit is not None else None,
            "status": state, "reasons": reasons, "ship_evidence": measured,
            "port_source_column": port.get("source_columns", {}).get("max_" + axis + "_m"),
        })
    has_excess = any(c["status"] == "exceeds_published_reference" for c in comparisons)
    complete = all(c["status"] != "unavailable" for c in comparisons)
    return {
        "version": VERSION, "vessel_id": ship["vessel_id"], "port_id": port["port_id"],
        "port_name": port["name"], "reference_only": True,
        "status": "reference_exceedance" if has_excess else
                  "reference_values_within_limits" if complete else "insufficient_reference_data",
        "comparisons": comparisons,
        "admission": "not_assessed", "safe_draft_m": None,
        "required_before_admission": [
            "CURRENT_VERIFIED_SHIP_LOA_AND_BEAM",
            "CURRENT_DRAFT_AND_OBSERVATION_TIME",
            "SELECTED_TERMINAL_BERTH_AND_APPROACH",
            "CURRENT_PORT_AUTHORITY_LIMITS_AND_NOTICES",
            "ETA_TIDE_WEATHER_UKC_AND_DYNAMIC_EFFECTS",
            "CARGO_TERMINAL_AND_PILOTAGE_REQUIREMENTS",
            "PORT_AUTHORITY_CONFIRMATION",
        ],
        "warnings": [
            "NOT_AN_ENTRY_PERMISSION_OR_NAVIGATION_RECOMMENDATION",
            "WIKIDATA_LENGTH_BASIS_AND_DIMENSION_EFFECTIVE_DATE_UNVERIFIED",
            "PORT_SNAPSHOT_EFFECTIVE_DATE_UNVERIFIED",
            "HISTORICAL_DRAFT_IS_NOT_CURRENT_DRAFT",
            "CHANNEL_DEPTH_AND_TIDAL_RANGE_DO_NOT_DEFINE_SAFE_DRAFT",
        ],
        "ship_source": ship.get("wikidata_source"), "port_source": port.get("source"),
    }
