"""Small input-validation and formatting helpers shared by the store and routes."""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone

from .errors import ApiError, require


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stamp(value):
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def text(value, label: str, maximum: int) -> str:
    require(isinstance(value, str) and 0 < len(value.strip()) <= maximum, f"{label}을(를) 확인해 주세요.")
    return value.strip()


def number(value, label: str, low: float, high: float) -> float:
    require(type(value) in (int, float) and low <= value <= high, f"{label}의 숫자 범위를 확인해 주세요.")
    return value


def iso_date(value, label="일자") -> str:
    require(isinstance(value, str) and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)), f"{label}를 확인해 주세요.")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ApiError(400, "INVALID_INPUT", f"{label}를 확인해 주세요.") from exc
    return value


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def public_user(user: dict | None) -> dict | None:
    return {key: value for key, value in user.items() if key != "password"} if user else None
