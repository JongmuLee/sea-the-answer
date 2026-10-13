"""Accounts, login, permission checks and the audit log."""

from __future__ import annotations

import hashlib
import hmac
import re
from uuid import uuid4

from psycopg.types.json import Jsonb

from ..errors import require
from ..validation import public_user, text, utc_now


class UserStore:
    def user_by_id(self, user_id: str) -> dict | None:
        return self.one("SELECT * FROM users WHERE id=%s AND active=true", (user_id,))

    def authenticate(self, username: str, password: str) -> dict:
        user = self.one("SELECT * FROM users WHERE username=%s AND active=true", (username,))
        valid = False
        if user:
            try:
                salt, expected = user["password"].split(":", 1)
                actual = hashlib.scrypt(str(password).encode()[:128], salt=salt.encode(), n=16384, r=8, p=1, dklen=64).hex()
                valid = hmac.compare_digest(actual, expected)
            except (ValueError, TypeError):
                pass
        require(valid, "아이디 또는 비밀번호가 올바르지 않습니다.", "LOGIN_FAILED", 401)
        self.audit(user, "auth", "로그인", username)
        return public_user(user)

    def audit(self, user: dict | None, category: str, action: str, target: str, detail=None) -> None:
        self.run("INSERT INTO audit VALUES(%s,%s,%s,%s,%s,%s,%s)",
                 (str(uuid4()), utc_now(), user.get("name", "시스템") if user else "시스템",
                  category, action, str(target), Jsonb(detail or {})))

    def permit(self, user: dict, roles=("admin",)) -> None:
        require(user["role"] in roles, "이 작업을 수행할 권한이 없습니다.", "FORBIDDEN", 403)

    def save_user(self, actor: dict, payload: dict) -> dict:
        self.permit(actor)
        username = text(payload.get("username"), "아이디", 40)
        require(bool(re.fullmatch(r"[A-Za-z0-9._-]{3,40}", username)), "아이디는 영문·숫자·점·밑줄·하이픈 3~40자입니다.")
        require(payload.get("role") in ("admin", "operator", "viewer"), "권한을 확인해 주세요.")
        old = self.one("SELECT * FROM users WHERE id=%s", (payload["id"],)) if payload.get("id") else None
        if payload.get("id"):
            require(old and old["version"] == payload.get("version"), "사용자 정보가 변경되었습니다.", "VERSION_CONFLICT", 409)
        require(not old or old["username"] != "admin", "기본 관리자 계정은 고정되어 있습니다.", "FORBIDDEN", 403)
        active = payload.get("active") is not False
        if old and old["role"] == "admin" and (not active or payload["role"] != "admin"):
            require(bool(self.one("SELECT 1 FROM users WHERE role='admin' AND active=true AND id<>%s", (old["id"],))),
                    "최소 한 명의 활성 관리자가 필요합니다.")
        password = old["password"] if old else None
        if payload.get("password"):
            plain = text(payload["password"], "비밀번호", 128)
            require(len(plain) >= 10, "비밀번호는 10자 이상 입력해 주세요.")
            salt = uuid4().hex
            password = salt + ":" + hashlib.scrypt(plain.encode(), salt=salt.encode(), n=16384, r=8, p=1, dklen=64).hex()
        require(password, "비밀번호를 입력해 주세요.")
        user_id = old["id"] if old else str(uuid4())
        require(not self.one("SELECT 1 FROM users WHERE username=%s AND id<>%s", (username, user_id)), "이미 사용 중인 아이디입니다.")
        self.run("""INSERT INTO users VALUES(%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(id) DO UPDATE SET username=excluded.username,name=excluded.name,
            role=excluded.role,password=excluded.password,active=excluded.active,version=excluded.version""",
            (user_id, username, text(payload.get("name"), "이름", 80), payload["role"],
             password, active, (old or {}).get("version", 0) + 1))
        self.audit(actor, "users", "사용자 변경" if old else "사용자 등록", username)
        return public_user(self.one("SELECT * FROM users WHERE id=%s", (user_id,)))
