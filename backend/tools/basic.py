"""Existing calculator arithmetic, separated from database/audit writes."""
from __future__ import annotations

import re
from datetime import datetime

from api.errors import ApiError, require
from api.validation import number


def calculate_emissions(payload: dict) -> dict:
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
    return output


def voyage_time(payload: dict) -> dict:
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
    return output
