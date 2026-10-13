"""Disposable PostgreSQL schema for document API tests; always rolled back."""
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from backend.pyapi.workspace_store import Workspace


@contextmanager
def isolated_workspace(dsn):
    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=5) as connection:
        with connection.transaction(force_rollback=True):
            schema = "rag_test_" + uuid4().hex
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            connection.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema)))
            connection.execute((Path(__file__).parents[1] / "pyapi/schema.sql").read_text(encoding="utf-8"))
            store = object.__new__(Workspace)
            store.db, store.dsn = connection, dsn
            store.bootstrap()

            class RequestWorkspace(Workspace):
                def __init__(self, _dsn):
                    self.db, self.dsn = connection, dsn

                def __enter__(self):
                    # Each request gets a savepoint. The outer transaction
                    # owns rollback and the connection; never commit it here.
                    self.request_transaction = connection.transaction()
                    self.request_transaction.__enter__()
                    return self

                def __exit__(self, *args):
                    return self.request_transaction.__exit__(*args)

            yield store, RequestWorkspace


def configure_api(monkeypatch, api, request_workspace):
    monkeypatch.setattr(api, "Workspace", request_workspace)
    # bootstrap above already ran inside the rollback-only transaction.
    monkeypatch.setattr(api, "initialized", True)
    monkeypatch.setattr(api, "sessions", {})
    monkeypatch.setattr(api, "login_attempts", {})


def login(client):
    health = client.get("/api/health")
    assert health.status_code == 200
    headers = {"X-Haedap-Token": health.json()["csrfToken"]}
    result = client.post("/api/auth/login", headers=headers, json={"username": "admin", "password": "1234"})
    assert result.status_code == 200
    return headers
