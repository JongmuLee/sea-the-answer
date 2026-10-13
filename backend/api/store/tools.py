"""Calculation tools and their run records (tool_runs)."""

from __future__ import annotations

import re
from datetime import datetime
from uuid import uuid4

from psycopg.types.json import Jsonb

from ..errors import ApiError, require
from ..validation import number, utc_now

TOOL_DEFINITIONS = [
    {"name": "calculate_emissions", "description": "입력한 연료와 배출계수로 배출량 및 단순 집약도를 계산합니다.",
     "input": {"fuel": "t, 0..1e7", "factor": "tCO2/t fuel, >0..10", "dwt": "t, >0..1e7", "distance": "nm, >0..1e7"}},
    {"name": "voyage_time", "description": "UTC 시각과 오프셋을 사용해 항해 시간을 계산합니다.",
     "input": {"start": "ISO 8601 UTC (...Z)", "end": "ISO 8601 UTC (...Z)",
               "before": "UTC offset minutes", "after": "UTC offset minutes"}},
]


class ToolStore:
    def calculate_emissions(self, payload: dict) -> dict:
        fuel = number(payload.get("fuel"), "연료(t)", 0, 1e7)
        factor = number(payload.get("factor"), "배출계수", 0.000001, 10)
        dwt = number(payload.get("dwt"), "DWT", 0.000001, 1e7)
        distance = number(payload.get("distance"), "거리(nm)", 0.000001, 1e7)
        output = {"version": "HAEDAP-EMISSIONS-1", "emission": fuel * factor,
                  "intensity": fuel * factor * 1e6 / (dwt * distance),
                  "units": {"emission": "tCO2", "intensity": "gCO2/(DWT·nm)"},
                  "formula": "fuel * factor; emission * 1e6 / (dwt * distance)",
                  "officialCiiRating": None,
                  "assumptions": ["배출계수는 입력값입니다.", "공식 CII 등급은 산정하지 않습니다."]}
        run_id = str(uuid4())
        self.run("INSERT INTO tool_runs VALUES(%s,'calculate_emissions',%s,%s,%s,%s)",
                 (run_id, output["version"], Jsonb(payload), Jsonb(output), utc_now()))
        return {"id": run_id, "name": "calculate_emissions", **output}

    def voyage_time(self, payload: dict) -> dict:
        def parse_utc(value, label):
            require(isinstance(value, str) and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{3})?Z", value)),
                    f"{label}: UTC ISO 시각이 필요합니다.")
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ApiError(400, "INVALID_INPUT", f"{label}를 확인해 주세요.") from exc
        start, end = parse_utc(payload.get("start"), "시작 시각"), parse_utc(payload.get("end"), "종료 시각")
        elapsed = (end - start).total_seconds() / 3600
        require(0 <= elapsed <= 366 * 24, "시각 순서와 기간을 확인해 주세요.")
        before = number(payload.get("before"), "시작 오프셋", -720, 840)
        after = number(payload.get("after"), "종료 오프셋", -720, 840)
        require(all(isinstance(value, int) and value % 15 == 0 for value in (before, after)), "오프셋은 15분 단위입니다.")
        from datetime import timedelta
        output = {"version": "HAEDAP-TIME-1", "elapsedHours": elapsed,
                  "clockHours": elapsed + (after - before) / 60,
                  "shipStart": (start + timedelta(minutes=before)).replace(tzinfo=None).isoformat(timespec="milliseconds"),
                  "shipEnd": (end + timedelta(minutes=after)).replace(tzinfo=None).isoformat(timespec="milliseconds"),
                  "interval": "[start,end)", "assumptions": ["선내 UTC 오프셋은 사용자가 입력합니다."]}
        run_id = str(uuid4())
        self.run("INSERT INTO tool_runs VALUES(%s,'voyage_time',%s,%s,%s,%s)",
                 (run_id, output["version"], Jsonb(payload), Jsonb(output), utc_now()))
        return {"id": run_id, "name": "voyage_time", **output}
