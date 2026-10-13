"""Request slots: at most 4 concurrent requests, and a backup restore runs alone.

The in-process counters guard one worker; PostgreSQL advisory locks guard across workers.
"""

from __future__ import annotations

import threading

from ..errors import require

REQUEST_LOCK_KEY = 782315201026
busy = threading.BoundedSemaphore(4)
request_lock = threading.Lock()
active_requests = 0
restoring = False


def database_request_lock(db, exclusive: bool = False) -> None:
    function = "pg_try_advisory_xact_lock" if exclusive else "pg_try_advisory_xact_lock_shared"
    row = db.one(f"SELECT {function}(%s) AS locked", (REQUEST_LOCK_KEY,))
    require(row and row["locked"], "다른 요청 또는 복구가 진행 중입니다.", "BUSY", 409)


def reserve_request(path: str) -> None:
    global active_requests, restoring
    with request_lock:
        if path == "backups/restore":
            require(active_requests == 0 and not restoring,
                    "진행 중인 요청이 있습니다. 잠시 뒤 복구해 주세요.", "BUSY", 409)
        else:
            require(not restoring, "복구가 진행 중입니다. 잠시 뒤 다시 시도해 주세요.", "BUSY", 409)
        require(busy.acquire(blocking=False), "처리 중인 요청이 많습니다.", "BUSY", 429)
        active_requests += 1
        if path == "backups/restore":
            restoring = True


def release_request(path: str) -> None:
    global active_requests, restoring
    with request_lock:
        active_requests -= 1
        if path == "backups/restore":
            restoring = False
        busy.release()
