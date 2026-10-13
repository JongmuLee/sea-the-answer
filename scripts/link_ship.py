"""Explicitly link one source vessel to an existing app ship in the same database."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

from dotenv import load_dotenv
import psycopg
from psycopg.rows import dict_row


class MappingError(ValueError):
    """An unsafe or unsupported mapping request; messages contain no credentials."""


def link_ship(connection, vessel_id: str, ship_id: str, *, apply=False, confirm_synthetic=False) -> dict:
    if not isinstance(vessel_id, str) or not vessel_id or len(vessel_id) > 200:
        raise MappingError("A valid source vessel ID is required.")
    if not isinstance(ship_id, str) or not ship_id or len(ship_id) > 200:
        raise MappingError("An existing app ship ID is required.")
    with connection.transaction(), connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("SET LOCAL lock_timeout = '5s'")
        cursor.execute("SET LOCAL statement_timeout = '5s'")
        # Keep the same lock order as workspace restore: maritime vessels, then
        # app ships. The table lock also serializes explicit offline mappings.
        cursor.execute("LOCK TABLE maritime_data.vessels IN SHARE ROW EXCLUSIVE MODE")
        vessel = cursor.execute("""SELECT vessel_id,imo_number,ship_id
            FROM maritime_data.vessels WHERE vessel_id=%s FOR UPDATE""", (vessel_id,)).fetchone()
        if not vessel:
            raise MappingError("The source vessel was not found; no changes applied.")
        if vessel["ship_id"] not in (None, ship_id):
            raise MappingError("The source vessel already has a different link; no changes applied.")
        app_ship = cursor.execute("SELECT body FROM public.ships WHERE id=%s FOR UPDATE", (ship_id,)).fetchone()
        if not app_ship or not isinstance(app_ship["body"], dict):
            raise MappingError("The app ship was not found or has invalid identity data; no changes applied.")
        body = app_ship["body"]
        if vessel_id.startswith("REAL:IMO:"):
            source_imo = str(vessel["imo_number"] or "").strip()
            app_imo = re.sub(r"^IMO\s*", "", str(body.get("imo") or "").strip(), flags=re.I)
            if (not re.fullmatch(r"[0-9]{7}", source_imo)
                    or vessel_id != "REAL:IMO:" + source_imo
                    or source_imo != app_imo or bool(body.get("sample"))):
                raise MappingError("Real vessels require the same source/app IMO and a non-sample app ship.")
        elif vessel_id.startswith("SYN:"):
            if not confirm_synthetic or body.get("sample") is not True:
                raise MappingError("Synthetic links require --confirm-synthetic and an app ship with sample=true.")
        else:
            raise MappingError("Only REAL:IMO: and SYN: source vessel IDs can be linked.")
        duplicate = cursor.execute("""SELECT 1 FROM maritime_data.vessels
            WHERE ship_id=%s AND vessel_id<>%s LIMIT 1""", (ship_id, vessel_id)).fetchone()
        if duplicate:
            raise MappingError("The app ship is already linked to another source vessel; no changes applied.")
        if vessel["ship_id"] == ship_id:
            status = "already_linked"
        elif apply:
            cursor.execute("""UPDATE maritime_data.vessels SET ship_id=%s
                WHERE vessel_id=%s AND ship_id IS NULL""", (ship_id, vessel_id))
            status = "linked"
        else:
            status = "preview"
    return {"status": status, "vessel_id": vessel_id, "ship_id": ship_id}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vessel-id", required=True)
    parser.add_argument("--ship-id", required=True)
    parser.add_argument("--apply", action="store_true", help="Apply the link (default: preview only)")
    parser.add_argument("--confirm-synthetic", action="store_true", help="Confirm a development-only synthetic mapping")
    parser.add_argument("--env-file", type=Path, default=Path(__file__).resolve().parents[1] / ".env")
    args = parser.parse_args(argv)
    try:
        load_dotenv(args.env_file, override=False)
        dsn = os.getenv("DATABASE_URL", "").replace("postgresql+psycopg://", "postgresql://", 1)
        if not dsn:
            raise MappingError("DATABASE_URL is required in the environment or the selected env file.")
        with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=5) as connection:
            result = link_ship(connection, args.vessel_id, args.ship_id,
                               apply=args.apply, confirm_synthetic=args.confirm_synthetic)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except MappingError as exc:
        print(str(exc), file=sys.stderr)
    except (psycopg.Error, OSError, ValueError):
        # Driver errors can contain a DSN/password or source values. Keep them
        # private; an operator can inspect PostgreSQL logs for detailed diagnosis.
        print("Mapping could not be checked or applied. Verify migrations, permissions and database availability.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
