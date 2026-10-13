"""PostgreSQL connection per request: transaction scope and query helpers."""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from ..errors import ApiError, require


class Database:
    def __init__(self, dsn: str):
        require(bool(dsn), "DATABASE_URL을 설정해 주세요.", "DATA_SOURCE_UNAVAILABLE", 503)
        self.dsn = dsn
        try:
            self.db = psycopg.connect(dsn, row_factory=dict_row, connect_timeout=5)
        except psycopg.Error as exc:
            raise ApiError(503, "DATA_SOURCE_UNAVAILABLE", "PostgreSQL에 연결할 수 없습니다.") from exc

    def __enter__(self):
        return self

    def __exit__(self, error_type, _error, _traceback):
        try:
            if error_type:
                self.db.rollback()
            else:
                self.db.commit()
        finally:
            self.db.close()

    def one(self, sql: str, params: tuple = ()) -> dict | None:
        return self.db.execute(sql, params).fetchone()

    def all(self, sql: str, params: tuple = ()) -> list[dict]:
        return list(self.db.execute(sql, params).fetchall())

    def run(self, sql: str, params: tuple = ()) -> None:
        self.db.execute(sql, params)
