"""Daily automatic backup, checked once a minute while the app runs."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from starlette.concurrency import run_in_threadpool

from .. import config, deps
from .concurrency import database_request_lock

logger = logging.getLogger(__name__)


def automatic_backup_once() -> None:
    with deps.open_workspace() as db:
        deps.initialize(db)
        database_request_lock(db)
        setting = db.one("SELECT value FROM settings WHERE key='autoBackup'")
        if not setting or setting["value"] != "daily":
            return
        db.run("INSERT INTO settings(key,value) VALUES('autoBackupDay','') ON CONFLICT(key) DO NOTHING")
        day_setting = db.one("SELECT value FROM settings WHERE key='autoBackupDay' FOR UPDATE")
        today = datetime.now(timezone.utc).date().isoformat()
        if day_setting["value"] == today:
            return
        admin = db.one("SELECT * FROM users WHERE role='admin' AND active=true LIMIT 1")
        if not admin:
            return
        db.create_backup(admin, config.backup_directory(), "정기 자동 백업")
        db.run("UPDATE settings SET value=%s WHERE key='autoBackupDay'", (today,))


async def automatic_backup_loop() -> None:
    while True:
        await asyncio.sleep(60)
        try:
            await run_in_threadpool(automatic_backup_once)
        except Exception:
            logger.exception("Automatic backup failed")
