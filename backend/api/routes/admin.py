"""Administration: users, audit log, backups and restore."""

from __future__ import annotations

from .. import config
from ..errors import require
from ..validation import public_user


def users(ctx):
    ctx.db.permit(ctx.user)
    return {"users": [public_user(row) for row in ctx.db.all("SELECT * FROM users ORDER BY username")]}


def save_user(ctx):
    return {"user": ctx.db.save_user(ctx.user, ctx.body)}


def logs(ctx):
    ctx.db.permit(ctx.user)
    return {"logs": [{**row, "at": row["at"].isoformat()} for row in ctx.db.all("SELECT * FROM audit ORDER BY at DESC LIMIT 500")]}


def backups(ctx):
    setting = ctx.db.one("SELECT value FROM settings WHERE key='autoBackup'")
    return {"backups": ctx.db.list_backups(ctx.user, config.backup_directory()),
            "autoBackup": setting["value"] if setting else "off"}


def read_backup(ctx):
    # GET backups/{id}
    return ctx.db.read_backup(ctx.user, config.backup_directory(), ctx.path[len("backups/"):])


def create_backup(ctx):
    return {"backup": ctx.db.create_backup(ctx.user, config.backup_directory())}


def backup_settings(ctx):
    db, body = ctx.db, ctx.body
    db.permit(ctx.user)
    require(body.get("autoBackup") in ("daily", "off"), "자동 백업 설정을 확인해 주세요.")
    db.run("INSERT INTO settings VALUES('autoBackup',%s) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (body["autoBackup"],))
    db.audit(ctx.user, "backup", "자동 백업 설정", body["autoBackup"])
    return {"ok": True}


def import_backup(ctx):
    return {"backup": ctx.db.import_backup(ctx.user, config.backup_directory(), ctx.body)}


def restore_backup(ctx):
    # Sessions are cleared by the entry point after a successful restore.
    return ctx.db.restore_backup(ctx.user, config.backup_directory(), ctx.body)


GET = {"users": users, "logs": logs, "backups": backups}
GET_PREFIX = [("backups/", read_backup)]
POST = {"users": save_user, "backups": create_backup, "backups/settings": backup_settings,
        "backups/import": import_backup, "backups/restore": restore_backup}
