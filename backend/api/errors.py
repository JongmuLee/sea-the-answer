"""API error type and the guard used everywhere for input/permission checks."""

from __future__ import annotations


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


def require(condition: bool, message: str, code: str = "INVALID_INPUT", status: int = 400) -> None:
    if not condition:
        raise ApiError(status, code, message)
