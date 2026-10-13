"""Admin login/logout. General users never log in (guest cookie only)."""

from __future__ import annotations

import time

from ..errors import ApiError, require
from ..web import security


def setup(_ctx):
    raise ApiError(410, "SETUP_DISABLED", "초기 설정은 필요하지 않습니다.")


def login(ctx):
    request, body = ctx.request, ctx.body
    remote = request.client.host if request.client else "local"
    attempts = security.login_attempts.get(remote, {"count": 0, "until": 0})
    if attempts["until"] < time.time():
        attempts = {"count": 0, "until": time.time() + 60}
    require(attempts["count"] < 10, "로그인 시도가 많습니다.", "LOGIN_LIMIT", 429)
    attempts["count"] += 1
    security.login_attempts[remote] = attempts
    admin = ctx.db.authenticate(body.get("username", ""), body.get("password", ""))
    require(admin["role"] == "admin", "관리자 계정으로 로그인해 주세요.", "FORBIDDEN", 403)
    security.login_attempts.pop(remote, None)
    security.end_session(request)
    security.start_session(ctx.response, admin)
    return {"user": admin}


def logout(ctx):
    security.end_session(ctx.request)
    ctx.response.delete_cookie("haedap_session")
    if ctx.user["role"] == "admin":
        ctx.db.audit(ctx.user, "auth", "로그아웃", ctx.user["username"])
    return {"ok": True, "user": ctx.guest}


POST = {"auth/setup": setup, "auth/login": login, "auth/logout": logout}
