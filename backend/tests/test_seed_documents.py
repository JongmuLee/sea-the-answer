"""Seed knowledge integrity and original-PDF attachment (no PostgreSQL required)."""

from __future__ import annotations

import base64
import json
import re

import pytest

from api.config import BACKEND_DIR
from api.store import Workspace

KNOWLEDGE = BACKEND_DIR / "knowledge"
SEED = json.loads((KNOWLEDGE / "seed.json").read_text(encoding="utf-8"))


def test_seed_entries_satisfy_document_validation_rules():
    ids = [entry["id"] for entry in SEED]
    assert len(ids) == len(set(ids))
    for entry in SEED:
        assert re.fullmatch(r"[a-z0-9][a-z0-9-]*", entry["id"]) and len(entry["id"]) <= 80
        assert entry["kind"] in ("official-summary", "onboard", "sample")
        if entry["kind"] == "official-summary":
            assert entry["url"].startswith("https://")
        assert 0 < len(entry["sections"]) <= 1500
        for section in entry["sections"]:
            assert section["heading"].strip() and len(section["heading"]) <= 200
            assert section["text"].strip() and len(section["text"]) <= 20000
            assert section.get("page") is None or type(section["page"]) is int


@pytest.mark.parametrize("entry", [e for e in SEED if "file" in e], ids=lambda e: e["id"])
def test_seed_files_are_bundled_pdfs(entry):
    path = (KNOWLEDGE / entry["file"]).resolve()
    assert path.is_relative_to(KNOWLEDGE.resolve()) and path.is_file()
    data = path.read_bytes()
    assert data.startswith(b"%PDF-") and len(data) <= 25 * 1024 * 1024


def test_seed_includes_statutes_handbook_and_glossary():
    by_id = {entry["id"]: entry for entry in SEED}
    for doc_id in ("kr-marine-environment-management-act", "kr-ship-safety-act",
                   "kr-maritime-traffic-safety-act", "kr-ship-officers-act", "uscg-navigation-rules-handbook"):
        assert by_id[doc_id]["file"].startswith("documents/")
    glossary = " ".join(section["text"] for section in by_id["maritime-glossary"]["sections"])
    for term in ("IMO", "MARPOL", "COLREG", "조종불능선", "선박운항탄소집약도지수"):
        assert term in glossary
    definitions = next(s["text"] for s in by_id["kr-maritime-traffic-safety-act"]["sections"]
                       if "제2조(정의)" in s["heading"])
    assert "“거대선”(巨大船)이란 길이 200미터 이상의 선박을 말한다." in definitions


class FakeBootstrap:
    """Just enough of Workspace for bootstrap(): an initialized DB without documents."""

    def __init__(self):
        self.saved = []

    def one(self, sql, params=()):
        return None if "FROM documents" in sql else {"ok": 1}

    def run(self, sql, params=()):
        return None

    def save_document(self, payload):
        self.saved.append(payload)
        return {"status": "created"}


def test_bootstrap_attaches_original_pdf_and_strips_file_field():
    fake = FakeBootstrap()
    Workspace.bootstrap(fake)
    assert [p["document"]["id"] for p in fake.saved] == [entry["id"] for entry in SEED]
    for payload, entry in zip(fake.saved, SEED):
        assert "file" not in payload["document"]
        if "file" in entry:
            original = (KNOWLEDGE / entry["file"]).read_bytes()
            assert base64.b64decode(payload["file"]["base64"]) == original
            assert payload["file"]["name"] == entry["file"].split("/")[-1]
        else:
            assert "file" not in payload


def test_bootstrap_rejects_seed_file_outside_knowledge(monkeypatch, tmp_path):
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "knowledge" / "seed.json").write_text(json.dumps([{**SEED[0], "file": "../secret.pdf"}]), "utf-8")
    (tmp_path / "secret.pdf").write_bytes(b"%PDF-1.4")
    monkeypatch.setattr("api.store.BACKEND_DIR", tmp_path)
    with pytest.raises(RuntimeError, match="Seed document file not found"):
        Workspace.bootstrap(FakeBootstrap())
