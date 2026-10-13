"""Companion to verify-rag-pdf.mjs; raw PDF input via stdin, no persisted rows."""
import base64
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from backend.pyapi import workspace_api
from backend.tests.rag_postgres_support import configure_api, isolated_workspace, login


def verify():
    sample = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    raw = {"id": "rag-local-pdf-check", "title": sample["file"]["name"], "kind": "onboard",
           "reference": "Local PDF verification", "version": "verification-only",
           "reviewedAt": "2026-10-13", "language": "en", "sections": sample["sections"]}
    with isolated_workspace(os.environ["HAEDAP_TEST_DATABASE_URL"]) as (store, request_workspace):
        with MonkeyPatch.context() as patch:
            configure_api(patch, workspace_api, request_workspace)
            client = TestClient(workspace_api.app, base_url="http://localhost:5173")
            headers = login(client)
            response = client.post("/api/changes", headers=headers, json={"kind": "document.save", "payload": {
                "document": raw, "expectedId": "", "meta": {"scope": "all", "status": "active"}, "file": sample["file"]}})
            assert response.status_code == 200, "Document upload API failed"
            doc = next(d for d in client.get("/api/documents").json()["documents"] if d["logical_id"] == raw["id"])
            chunks = doc["sections"]
            assert {c["page"] for c in chunks} == {s["page"] for s in raw["sections"]}, "Page provenance failed"
            assert all(0 < len(c["text"]) <= 900 for c in chunks), "Chunk length failed"
            verified = []
            for page in sorted({c["page"] for c in chunks}):
                text = " ".join(c["text"] for c in chunks if c["page"] == page).lower()
                others = " ".join(c["text"] for c in chunks if c["page"] != page).lower()
                terms = [t for t in dict.fromkeys(re.findall(r"[a-z]{7,}", text))
                         if t not in others and t not in raw["title"].lower() and t not in raw["reference"].lower()]
                assert terms, "Selected pages need distinct search terms"
                evidence = client.post("/api/search", headers=headers, json={"question": terms[0]}).json()["evidence"]
                assert any(e["document_id"] == doc["id"] and e["page"] == page and e["title"] == raw["title"]
                           and e["heading"] and e["text"] for e in evidence), "Search provenance failed"
                verified.append(page)
            original = client.get(f'/api/documents/{doc["id"]}/pdf')
            assert original.status_code == 200 and original.content == base64.b64decode(sample["file"]["base64"]), "PDF round-trip failed"
            old_cuts = sum(1 for s in raw["sections"] for i in range(900, len(s["text"]), 900)
                           if s["text"][i-1].isalnum() and s["text"][i].isalnum())
            result = {"api_postgresql_search": "PASS", "verified_pages": verified, "chunks": len(chunks),
                      "old_fixed_split_midword_boundaries": old_cuts, "pdf_roundtrip": "PASS"}
    print(json.dumps({**result, "temporary_schema": "rolled back"}))


if __name__ == "__main__":
    try:
        verify()
    except Exception as error:
        # Never expose source text, SQL parameters or connection strings.
        print(f"Verification failed ({type(error).__name__}); temporary DB changes rolled back.", file=sys.stderr)
        sys.exit(1)
