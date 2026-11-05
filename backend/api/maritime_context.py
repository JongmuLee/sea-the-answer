"""Resolve app-owned ship identity through the explicit maritime mapping."""
from dataclasses import dataclass
import re

from sqlalchemy import text

from tools.port_reference import valid_imo
from .errors import require


@dataclass(frozen=True)
class RealShipContext:
    ship_id: str
    version: int
    body: dict
    vessel_id: str | None
    imo_number: str | None


def resolve_real_ship(db, engine, ship_id):
    row = db.one("SELECT body,version FROM ships WHERE id=%s", (ship_id,))
    require(row, "선택한 앱 선박이 없습니다.")
    with engine.connect() as connection, connection.begin():
        connection.execute(text("SET TRANSACTION READ ONLY"))
        connection.execute(text("SET LOCAL statement_timeout='5s'"))
        candidates = connection.execute(text("""SELECT vessel_id,imo_number
            FROM maritime_data.vessels WHERE ship_id=:ship AND data_origin='REAL' LIMIT 2"""),
            {"ship": ship_id}).mappings().all()
    if len(candidates) != 1:
        return RealShipContext(ship_id, row.get("version", 0), row["body"], None, None)
    candidate = candidates[0]
    imo = re.sub(r"^IMO\s*", "", str(row["body"].get("imo", "")).strip(), flags=re.I)
    require(not row["body"].get("sample") and valid_imo(imo)
            and imo == candidate["imo_number"] and candidate["vessel_id"] == "REAL:IMO:" + imo,
            "앱 선박과 MRV 선박의 정체성이 일치하지 않습니다. 연결을 검토해 주세요.",
            "MARITIME_SHIP_IDENTITY_CONFLICT", 409)
    return RealShipContext(ship_id, row.get("version", 0), row["body"], candidate["vessel_id"], imo)
