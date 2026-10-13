"""Opening a Workspace and one-time initialization (admin account, seed documents).

Tests replace `Workspace` here to run the HTTP layer without PostgreSQL.
"""

from __future__ import annotations

import threading

from . import config
from .store import Workspace

INITIALIZE_LOCK_KEY = 782315201027
initialize_lock = threading.Lock()
initialized = False


def open_workspace():
    return Workspace(config.database_url())


def initialize(db) -> None:
    global initialized
    if initialized:
        return
    with initialize_lock:
        if not initialized:
            db.one("SELECT pg_advisory_xact_lock(%s)", (INITIALIZE_LOCK_KEY,))
            db.bootstrap()
            db.db.commit()
            initialized = True
