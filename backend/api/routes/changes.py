"""Admin changes to source data and the approval queue."""

from __future__ import annotations


def list_changes(ctx):
    return {"changes": ctx.db.list_changes(ctx.user)}


def change(ctx):
    return ctx.db.change(ctx.user, ctx.body.get("kind", ""), ctx.body.get("payload"))


def review(ctx):
    return ctx.db.review_change(ctx.user, ctx.body)


GET = {"changes": list_changes}
POST = {"changes": change, "changes/review": review}
