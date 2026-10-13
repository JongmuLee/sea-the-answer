"""Whole-workspace JSON backups: create, list, import and restore."""

from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..errors import require
from ..validation import sha256, stamp, utc_now

BACKUP_TABLES = (
    "documents", "chunks", "reports", "tool_runs", "queries", "users",
    "document_meta", "document_files", "chunk_pages", "ships", "operations",
    "report_meta", "audit", "query_owner", "changes", "settings",
)


class BackupStore:
    @staticmethod
    def _maritime_ship_matches(link: dict, body: dict) -> bool:
        """A restored app ID alone is not evidence that it is the same vessel."""
        if not isinstance(body, dict):
            return False
        vessel_id = str(link.get("vessel_id") or "")
        source_imo = str(link.get("imo_number") or "").strip()
        if vessel_id.startswith("REAL:IMO:"):
            restored_imo = re.sub(r"^IMO\s*", "", str(body.get("imo") or "").strip(), flags=re.I)
            return (bool(re.fullmatch(r"[0-9]{7}", source_imo))
                    and vessel_id == "REAL:IMO:" + source_imo
                    and restored_imo == source_imo and not body.get("sample"))
        if not vessel_id.startswith("SYN:"):
            return False
        # Synthetic vessels have no authoritative IMO. Preserve only a previously
        # explicit link whose identity fields survive the restore unchanged.
        before = link.get("body")
        if not isinstance(before, dict) or not all(before.get(key) for key in ("name", "type", "dwt")):
            return False
        return (all(before.get(key) == body.get(key) for key in ("name", "type", "dwt"))
                and str(before.get("imo") or "").strip() == str(body.get("imo") or "").strip()
                and bool(before.get("sample")) == bool(body.get("sample")))

    def _maritime_links_before_restore(self) -> list[dict] | None:
        ready = self.one("""SELECT EXISTS (
            SELECT 1 FROM pg_attribute
            WHERE attrelid=to_regclass('maritime_data.vessels')
              AND attname='ship_id' AND NOT attisdropped
        ) AS ready""")
        if not ready["ready"]:
            return None
        # Offline mapping scripts do not share the API advisory lock. Prevent a
        # new mapping from appearing between this snapshot and DELETE ships.
        self.run("LOCK TABLE maritime_data.vessels IN SHARE ROW EXCLUSIVE MODE")
        return self.all("""SELECT v.vessel_id, v.ship_id, v.imo_number, s.body
            FROM maritime_data.vessels v LEFT JOIN public.ships s ON s.id=v.ship_id
            WHERE v.ship_id IS NOT NULL""")

    def _restore_maritime_links(self, links: list[dict]) -> dict:
        ships = {row["id"]: row["body"] for row in self.all("SELECT id,body FROM public.ships")}
        counts = {"linkedBefore": len(links), "restored": 0, "cleared": 0,
                  "missingShips": 0, "identityMismatch": 0}
        for link in links:
            if link["ship_id"] not in ships:
                counts["missingShips"] += 1
            elif not self._maritime_ship_matches(link, ships[link["ship_id"]]):
                counts["identityMismatch"] += 1
            else:
                # DELETE ships has set the FK to NULL. No source/quality rows or
                # source identities are rewritten by this app-only restore.
                self.run("""UPDATE maritime_data.vessels SET ship_id=%s
                    WHERE vessel_id=%s AND ship_id IS NULL""", (link["ship_id"], link["vessel_id"]))
                counts["restored"] += 1
        counts["cleared"] = counts["missingShips"] + counts["identityMismatch"]
        return counts

    @staticmethod
    def backup_path(directory: Path, backup_id: str) -> Path:
        require(bool(re.fullmatch(r"[a-f0-9-]{36}", backup_id)), "백업 ID를 확인해 주세요.")
        return directory / (backup_id + ".json")

    def create_backup(self, user: dict, directory: Path, label="수동 백업") -> dict:
        self.permit(user)
        directory.mkdir(parents=True, exist_ok=True)
        tables = {}
        # One repeatable-read snapshot keeps all tables at the same point in time.
        with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5) as snapshot_db:
            snapshot_db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            for table in BACKUP_TABLES:
                rows = snapshot_db.execute(f"SELECT * FROM {table}").fetchall()
                tables[table] = [{key: stamp(value) for key, value in row.items()} for row in rows]
        payload = {"format": "haedap-workspace", "version": 2, "createdAt": utc_now(),
                   "label": label, "tables": tables}
        checksum = sha256(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        backup_id = str(uuid4())
        temp = directory / (backup_id + ".tmp")
        temp.write_text(json.dumps({**payload, "checksum": checksum}, ensure_ascii=False), encoding="utf-8")
        temp.replace(self.backup_path(directory, backup_id))
        self.audit(user, "backup", "전체 백업 생성", backup_id, {"label": label})
        return {"id": backup_id, "createdAt": payload["createdAt"], "version": 2, "label": label}

    def list_backups(self, user: dict, directory: Path) -> list[dict]:
        self.permit(user)
        directory.mkdir(parents=True, exist_ok=True)
        result = []
        for path in directory.glob("*.json"):
            if not re.fullmatch(r"[a-f0-9-]{36}\.json", path.name):
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                result.append({"id": path.stem, "createdAt": payload["createdAt"],
                               "version": payload["version"], "label": payload["label"],
                               "counts": {key: len(payload["tables"][key]) for key in ("documents", "operations", "reports")}})
            except (OSError, KeyError, ValueError, TypeError):
                pass
        return sorted(result, key=lambda item: item["createdAt"], reverse=True)

    def read_backup(self, user: dict, directory: Path, backup_id: str) -> dict:
        self.permit(user)
        path = self.backup_path(directory, backup_id)
        require(path.is_file(), "백업을 찾을 수 없습니다.", "NOT_FOUND", 404)
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def validate_backup(snapshot: dict) -> dict:
        require(isinstance(snapshot, dict), "백업 형식을 확인해 주세요.")
        payload = {key: value for key, value in snapshot.items() if key != "checksum"}
        checksum = sha256(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        require(payload.get("format") == "haedap-workspace" and payload.get("version") == 2
                and snapshot.get("checksum") == checksum, "백업 형식 또는 무결성 검사에 실패했습니다.")
        tables = payload.get("tables")
        require(isinstance(tables, dict) and all(isinstance(tables.get(table), list) for table in BACKUP_TABLES),
                "백업에 필수 데이터가 없습니다.")
        require(any(user.get("role") == "admin" and user.get("active") for user in tables["users"]),
                "활성 관리자 계정이 없습니다.")
        return payload

    def import_backup(self, user: dict, directory: Path, snapshot: dict) -> dict:
        self.permit(user)
        payload = self.validate_backup(snapshot)
        directory.mkdir(parents=True, exist_ok=True)
        backup_id = str(uuid4())
        temp = directory / (backup_id + ".tmp")
        temp.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
        temp.replace(self.backup_path(directory, backup_id))
        self.audit(user, "backup", "백업 파일 가져오기", backup_id)
        return {"id": backup_id, "createdAt": payload["createdAt"],
                "version": 2, "label": payload["label"]}

    def restore_backup(self, user: dict, directory: Path, payload: dict) -> dict:
        self.permit(user)
        require(payload.get("confirm") == "복구", "복구 확인 문구를 입력해 주세요.")
        snapshot = self.read_backup(user, directory, payload.get("id", ""))
        data = self.validate_backup(snapshot)
        safety = self.create_backup(user, directory, "복구 직전 자동 보관")
        maritime_links = self._maritime_links_before_restore()
        for table in reversed(BACKUP_TABLES):
            self.run(f"DELETE FROM {table}")
        for table in BACKUP_TABLES:
            for row in data["tables"][table]:
                columns = list(row)
                placeholders = ",".join("%s" for _ in columns)
                values = [Jsonb(row[key]) if key in ("body", "sources", "input", "output", "response", "detail", "payload") and isinstance(row[key], (dict, list))
                          else bool(row[key]) if key == "active" else row[key] for key in columns]
                self.run(f"INSERT INTO {table}({','.join(columns)}) VALUES({placeholders})", tuple(values))
        detail = {"safetyBackup": safety["id"]}
        warnings = []
        if maritime_links is not None:
            detail["maritimeLinks"] = self._restore_maritime_links(maritime_links)
            if detail["maritimeLinks"]["cleared"]:
                warnings.append("복구된 선박이 없거나 식별정보가 달라 해사 데이터 연결 "
                                f"{detail['maritimeLinks']['cleared']}건을 해제했습니다. 원본 해사 데이터는 유지됩니다.")
        self.audit(user, "backup", "전체 복구", payload["id"], detail)
        return {"ok": True, **detail, **({"warnings": warnings} if warnings else {})}
