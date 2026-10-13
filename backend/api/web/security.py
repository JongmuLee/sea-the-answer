"""Who is calling and from where: Host/Origin checks, guest cookie, admin sessions, CSRF token."""

from __future__ import annotations

import hashlib
import re
import time
from secrets import token_hex

from fastapi import Request, Response

from .. import config
from ..errors import require
from ..validation import public_user

SESSION_SECONDS = 43200
csrf_token = token_hex(32)
# Admin sessions live in process memory (sha256(cookie) -> {id, version, expires}).
sessions: dict[str, dict] = {}
login_attempts: dict[str, dict] = {}


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def guest_identity(request: Request, response: Response) -> dict:
    cookie = request.cookies.get("haedap_guest")
    if not cookie or not re.fullmatch(r"[a-f0-9]{64}", cookie):
        cookie = token_hex(32)
        response.set_cookie("haedap_guest", cookie, max_age=31536000, httponly=True, samesite="strict")
    guest_id = "guest_" + digest(cookie)
    return {"id": guest_id, "username": "", "name": "일반 사용자 · " + guest_id[6:14],
            "role": "guest", "active": True, "version": 1}


def current_user(db, request: Request, guest: dict) -> dict:
    cookie = request.cookies.get("haedap_session")
    if not cookie:
        return guest
    session = sessions.get(digest(cookie))
    if not session or session["expires"] < time.time():
        return guest
    user = db.user_by_id(session["id"])
    return public_user(user) if user and user["version"] == session["version"] else guest


def start_session(response: Response, user: dict) -> None:
    token = token_hex(32)
    sessions[digest(token)] = {"id": user["id"], "version": user["version"], "expires": time.time() + SESSION_SECONDS}
    response.set_cookie("haedap_session", token, max_age=SESSION_SECONDS, httponly=True, samesite="strict")


def end_session(request: Request) -> None:
    cookie = request.cookies.get("haedap_session")
    if cookie:
        sessions.pop(digest(cookie), None)


def validate_origin(request: Request) -> None:
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    match = re.fullmatch(r"([A-Za-z0-9.-]+)(?::\d+)?", host)
    require(bool(match and match.group(1).lower() in config.allowed_hosts()),
            "접속 주소를 확인해 주세요.", "HOST_DENIED", 403)
    origin = request.headers.get("origin")
    require(not origin or origin == "http://" + host, "동일 출처 요청만 허용합니다.", "ORIGIN_DENIED", 403)
    require(request.headers.get("sec-fetch-site") not in ("cross-site", "same-site"),
            "동일 출처 요청만 허용합니다.", "ORIGIN_DENIED", 403)


def authenticate_request(db, request: Request, response: Response) -> tuple[dict, dict]:
    validate_origin(request)
    guest = guest_identity(request, response)
    user = current_user(db, request, guest)
    identity = request.headers.get("x-haedap-identity")
    if request.url.path != "/api/health" and not request.url.path.startswith("/api/auth/") and identity:
        require(identity == user["id"], "사용자 상태가 바뀌었습니다.", "AUTH_REQUIRED", 401)
    return user, guest
