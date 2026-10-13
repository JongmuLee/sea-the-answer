"""Settings read from the environment, and paths inside backend/."""

from __future__ import annotations

import os
from pathlib import Path

VERSION = "1.4.0"
# backend/ — the Python project root (contains pyproject.toml, knowledge/, data/).
BACKEND_DIR = Path(__file__).resolve().parents[1]


def database_url() -> str:
    return os.getenv("DATABASE_URL", "")


def backup_directory() -> Path:
    value = os.getenv("HAEDAP_BACKUP_DIR")
    return Path(value).resolve() if value else BACKEND_DIR / "data/backups"


def allowed_hosts() -> set[str]:
    hosts = {"127.0.0.1", "localhost"}
    hosts.update(item.strip().lower() for item in os.getenv("HAEDAP_ALLOWED_HOSTS", "").split(",") if item.strip())
    return hosts


def llm_configured() -> bool:
    return bool(os.getenv("OPENAI_API_KEY") and os.getenv("OPENAI_MODEL"))
