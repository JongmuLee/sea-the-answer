"""Integrated answers (/api/ask), optional grounded LLM statements, criterion checks, history."""

from __future__ import annotations

import re
from uuid import uuid4

import httpx
from psycopg.types.json import Jsonb

from .. import config, llm
from ..errors import require
from ..validation import number, stamp, text, utc_now


class AnswerStore:
    def compare_criterion(self, user: dict, criterion: dict | None, summary: dict) -> dict:
        unknown = {"status": "unknown", "label": "판단 불가", "reason": "검증한 기준과 적용 조건이 지정되지 않았습니다."}
        if not criterion:
            return unknown
        require(criterion.get("confirmed") is True, "기준 적용 조건을 확인해 주세요.")
        doc_id, chunk_id = criterion.get("documentId"), criterion.get("chunkId")
        require(doc_id in self.allowed_document_ids(user), "열람 가능한 적용 문서가 필요합니다.", "FORBIDDEN", 403)
        chunk = self.one("SELECT text FROM chunks WHERE id=%s AND document_id=%s", (chunk_id, doc_id))
        quote = criterion.get("quote")
        require(chunk and isinstance(quote, str) and len(quote) >= 8 and quote in chunk["text"], "기준 원문 구절을 확인해 주세요.")
        metric, operator = criterion.get("metric"), criterion.get("operator")
        require(metric in ("fuel", "emission", "intensity", "speed") and operator in ("lte", "gte"), "비교 조건을 확인해 주세요.")
        limit = number(criterion.get("limit"), "기준값", 0, 1e12)
        actual = summary[metric]
        if not summary["count"] or actual is None:
            return {**unknown, "reason": "해당 기간에 비교 가능한 운항 데이터가 없습니다."}
        passed = actual <= limit if operator == "lte" else actual >= limit
        return {"status": "met" if passed else "unmet", "label": "입력 기준 충족" if passed else "입력 기준 미충족",
                "actual": actual, "limit": limit, "rule": criterion,
                "reason": "사용자가 확인·입력한 단일 기준과의 수치 비교입니다. 공식 판정은 아닙니다."}

    def answer(self, user: dict, payload: dict) -> dict:
        question = text(payload.get("question"), "질문", 2000)
        language = payload.get("language", "auto")
        require(language in ("auto", "ko", "en"), "언어를 확인해 주세요.")
        language = "ko" if language == "auto" and re.search(r"[가-힣]", question) else "en" if language == "auto" else language
        mode = payload.get("mode", "extractive")
        require(mode in ("extractive", "llm"), "답변 모드를 확인해 주세요.")
        task = payload.get("task", "auto")
        require(task in ("auto", "documents", "operations", "integrated", "report"), "질문 유형을 확인해 주세요.")
        needs_ops = task in ("operations", "integrated", "report") or task == "auto" and bool(re.search(
            r"운항|연료|배출|거리|속력|선박|항차|추이|계산|보고서|noon|mrv|vessel|ship|fuel|emission|distance|speed|report", question, re.I))
        evidence = [] if task == "operations" else self.search(user, question, payload.get("filter", "all"))
        statements = [{"text": item["text"], "chunkId": item["id"], "quote": item["text"]} for item in evidence[:3]]
        warnings = []
        generation = "extractive"
        if mode == "llm" and evidence:
            if not config.llm_configured():
                warnings.append("MODEL_NOT_CONFIGURED")
            else:
                try:
                    statements = self.grounded_statements(question, evidence, language)
                    generation = "llm"
                except (ValueError, KeyError, httpx.HTTPError):
                    warnings.append("MODEL_FAILED_EXTRACTIVE_FALLBACK")
        runs = []
        if payload.get("calculation") is not None:
            runs.append(self.calculate_emissions(payload["calculation"]))
        summary = None
        notice = "검색된 문단을 그대로 표시합니다. 인용과 적용 조건을 확인하세요."
        operation_notice = None
        compliance = None
        if needs_ops:
            context = payload.get("context") or {}
            if not context.get("ship") or not context.get("from") or not context.get("to"):
                operation_notice = "운항 분석을 위해 선박과 기간을 선택해 주세요."
                warnings.append("OPERATIONS_CONTEXT_REQUIRED")
            else:
                summary = self.operation_summary(context)
                compliance = self.compare_criterion(user, payload.get("criterion"), summary)
                if not summary["count"]:
                    warnings.append("OPERATIONS_NOT_FOUND")
        status = "data_found" if summary and summary["count"] else "evidence_found" if statements else "insufficient_evidence"
        if status == "insufficient_evidence":
            notice = "근거 또는 운항 데이터가 부족합니다. 담당자에게 확인해 주세요."
        answer_id = str(uuid4())
        answer = {"id": answer_id, "question": question, "task": "integrated" if needs_ops else "documents",
                  "language": language, "generation": generation, "status": status, "notice": notice,
                  "warnings": warnings, "statements": statements, "evidence": evidence, "toolRuns": runs,
                  "reportSuggested": task == "report" or bool(re.search(r"보고서|noon|mrv|report", question, re.I))}
        if summary is not None:
            answer["operations"] = summary
            answer["compliance"] = compliance
        if operation_notice:
            answer["operationNotice"] = operation_notice
        self.run("INSERT INTO queries VALUES(%s,%s,%s,%s)", (answer_id, question, Jsonb(answer), utc_now()))
        self.run("INSERT INTO query_owner VALUES(%s,%s)", (answer_id, user["id"]))
        self.audit(user, "query", "질의", answer_id, {"question": question, "task": answer["task"]})
        return answer

    @staticmethod
    def grounded_statements(question: str, evidence: list[dict], language: str) -> list[dict]:
        settings = config.llm_settings()
        if settings is None:
            raise ValueError("Model not configured")
        return llm.grounded_statements(question, evidence, language, settings)

    def history(self, user: dict) -> list[dict]:
        rows = self.all("""SELECT q.* FROM queries q JOIN query_owner o ON o.id=q.id
            WHERE o.owner=%s ORDER BY q.created_at DESC LIMIT 100""", (user["id"],))
        result = []
        for row in rows:
            answer = row["response"]
            visible = all(self.can_read_document(user, item["document_id"]) for item in answer.get("evidence", []))
            result.append({"id": row["id"], "question": row["question"], "at": stamp(row["created_at"]),
                           "answer": answer if visible else None, "unavailable": not visible})
        return result
