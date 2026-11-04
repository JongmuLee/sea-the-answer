"""Measure fixed-question generation as supplied evidence count/length changes.

Additional evidence is copied from distinct checked-in seed sections, not a
synthetic growing corpus. This measures no retrieval, database, auth or browser
work. Citation validity never determines semantic correctness.
"""

from __future__ import annotations

import argparse
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx

from scripts import evaluate_llm as evaluation

config, llm = evaluation.config, evaluation.llm
SCENARIOS = {"one": (1, False), "three": (3, False), "five": (5, False), "five-long": (5, True)}
DEFAULT_CASE_IDS = ("sulphur-limits", "unsupported-captain-name")
STAT_KEYS = ("candidateEvidence", "usedEvidence", "omittedEvidence", "payloadBytes", "payloadByteBudget")
OUTCOMES = ("answered", "insufficient", "model_error", "input_rejected")
EXTRACTIVE_EXCERPT_CHARS = 4000


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def build_matrix(cases: list[dict], scenarios: list[str] | tuple[str, ...] = tuple(SCENARIOS),
                 *, seed_path: Path = evaluation.DEFAULT_SEED) -> list[dict]:
    """Keep each labelled source intact and add actual sections deterministically."""
    if not cases or not scenarios or any(name not in SCENARIOS for name in scenarios):
        raise ValueError("Select known cases and scenarios")
    if len({case["id"] for case in cases}) != len(cases) or len(set(scenarios)) != len(scenarios):
        raise ValueError("Duplicate matrix selection")
    documents = json.loads(Path(seed_path).read_text(encoding="utf-8"))
    pool = [{"id": f"{doc['id']}:load:{index}", "document_id": doc["id"],
             "title": doc["title"], "heading": section["heading"], "page": section.get("page"),
             "text": section["text"]}
            for doc in documents for index, section in enumerate(doc.get("sections", []))
            if isinstance(section.get("text"), str) and len(section["text"].strip()) >= 8]
    rows = []
    for case in cases:
        # Current labelled cases each have one source. Do not silently remove a
        # source if that fixture contract changes later.
        if len(case["evidence"]) != 1:
            raise ValueError("Load scenarios require exactly one labelled source")
        original = case["evidence"][0]
        candidates = [item for item in pool if (item["document_id"], item["heading"])
                      != (original["document_id"], original["heading"])]
        for name in scenarios:
            count, long = SCENARIOS[name]
            ordered = sorted(candidates, key=lambda item: (
                -len(item["text"].encode("utf-8")) if long else len(item["text"].encode("utf-8")),
                item["document_id"], item["id"]))
            if len(ordered) < count - 1:
                raise ValueError("Not enough distinct seed sections")
            evidence = deepcopy([original, *ordered[:count - 1]])
            rows.append({"id": f"{case['id']}/{name}", "case_id": case["id"], "scenario": name,
                         "question": case["question"], "expected": deepcopy(case["expected"]),
                         "labelled_evidence_ids": [original["id"]], "evidence": evidence,
                         "additional_section_policy": "distinct_seed_sections_not_labelled_irrelevant"})
    return rows


def evaluate(matrix: list[dict], mode: str, *, settings: config.LLMSettings | None = None) -> dict:
    if not matrix or mode not in {"extractive", "llm"} or mode == "llm" and settings is None:
        raise ValueError("Select a mode and explicit model configuration")
    started_at = datetime.now(timezone.utc).isoformat()
    results = []
    for case in matrix:
        evidence = case["evidence"]
        diagnostics = {"modelCallAttempted": False}
        started = perf_counter()
        statements, selected, error_type, excerpt_truncated = [], [], None, False
        try:
            selected, stats = llm.bound_evidence(case["question"], evidence)
            diagnostics.update(stats)
            if not selected:
                raise llm.InputBudgetExceeded("No complete evidence fits")
            if mode == "llm":
                statements = llm.grounded_statements(case["question"], evidence, "ko", settings,
                                                    diagnostics=diagnostics)
            else:
                # Input snapshots stay intact. Bound only the displayed offline
                # excerpt to the same output limits as production statements.
                excerpt_truncated = any(len(item["text"]) > EXTRACTIVE_EXCERPT_CHARS for item in selected)
                statements = [{"text": item["text"][:EXTRACTIVE_EXCERPT_CHARS], "chunkId": item["id"],
                               "quote": item["text"][:EXTRACTIVE_EXCERPT_CHARS]}
                              for item in selected]
            llm.validate_statements({"insufficient": not statements, "statements": statements}, selected)
            outcome = "answered" if statements else "insufficient"
            citation_valid = True if statements else None
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            outcome = "input_rejected" if isinstance(exc, llm.InputBudgetExceeded) else "model_error"
            statements, citation_valid, error_type = [], False, type(exc).__name__
        elapsed = round(perf_counter() - started, 4)
        attempted = diagnostics["modelCallAttempted"] is True
        used_ids = [item["id"] for item in selected]
        selected_input = {"question": case["question"], "evidence": [
            {"chunkId": item["id"], "title": item["title"], "text": item["text"]} for item in selected]}
        results.append({"id": case["id"], "case_id": case["case_id"], "scenario": case["scenario"],
                        "question": case["question"], "expected": case["expected"],
                        "labelled_evidence_ids": case["labelled_evidence_ids"],
                        "source_evidence": evidence,
                        "additional_section_policy": case["additional_section_policy"],
                        "supplied_evidence_count": len(evidence),
                        "supplied_text_bytes": sum(len(item["text"].encode("utf-8")) for item in evidence),
                        "used_evidence_ids": used_ids,
                        "omitted_evidence_ids": [item["id"] for item in evidence if item["id"] not in used_ids],
                        "labelled_evidence_included": all(key in used_ids for key in case["labelled_evidence_ids"]),
                        "selected_input_sha256": fingerprint(selected_input),
                        "generationStats": {key: diagnostics.get(key) for key in STAT_KEYS},
                        "input_sha256": fingerprint(case), "outcome": outcome, "error_type": error_type,
                        "duration_seconds": elapsed,
                        "extractive_output_truncated": excerpt_truncated,
                        "generation_seconds": elapsed if mode == "llm" and attempted else None,
                        "model_call_attempted": attempted,
                        "generation": mode if outcome in {"answered", "insufficient"} else None,
                        "statements": statements, "citation_valid": citation_valid,
                        "expected_insufficient_matches":
                            outcome == ("insufficient" if case["expected"]["insufficient"] else "answered")
                            if outcome in {"answered", "insufficient"} else None,
                        "semantic_review": "pending"})
    attempted = any(row["model_call_attempted"] for row in results)
    return {"schema_version": 1, "started_at": started_at, "mode": mode,
            "execution": "real_model" if attempted else "extractive" if mode == "extractive" else "no_model_calls",
            "model_call_attempted": attempted, "model": settings.model if mode == "llm" else None,
            "response_format": settings.response_format if mode == "llm" else None,
            "evaluation_scope": "fixed_question_added_seed_sections_without_retrieval_db_auth_or_browser",
            "timing_scope": "evidence_budget_and_generation_validation_not_search_or_full_api",
            "input_units": "UTF-8 bytes, not model tokens",
            "extractive_excerpt_limit_chars": EXTRACTIVE_EXCERPT_CHARS,
            "llm_code_sha256": hashlib.sha256(Path(llm.__file__).read_bytes()).hexdigest(),
            "evaluation_input_sha256": fingerprint(matrix), "semantic_review": "pending",
            "summary": {"cases": len(results), **{name: sum(row["outcome"] == name for row in results)
                                                     for name in OUTCOMES},
                        "model_calls": sum(row["model_call_attempted"] for row in results)},
            "results": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("extractive", "llm"), default="extractive")
    parser.add_argument("--case", action="append", dest="case_ids", default=[])
    parser.add_argument("--scenario", action="append", choices=tuple(SCENARIOS), default=[])
    parser.add_argument("--output", type=Path, help="Create a new UTF-8 JSON file; never overwrite")
    args = parser.parse_args(argv)
    try:
        cases = evaluation.load_cases()
        selected_ids = set(args.case_ids or DEFAULT_CASE_IDS)
        if selected_ids - {case["id"] for case in cases}:
            raise ValueError("Unknown evaluation case")
        selected_scenarios = set(args.scenario or SCENARIOS)
        matrix = build_matrix([case for case in cases if case["id"] in selected_ids],
                              [name for name in SCENARIOS if name in selected_scenarios])
        if args.output is not None and args.output.exists():
            raise ValueError("Output file already exists")
        settings = config.llm_settings() if args.mode == "llm" else None
        if args.mode == "llm" and settings is None:
            raise ValueError("Model configuration is missing or invalid")
        destination = (args.output.open("x", encoding="utf-8", newline="\n")
                       if args.output is not None else nullcontext(sys.stdout))
        with destination as stream:
            report = evaluate(matrix, args.mode, settings=settings)
            stream.write(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        return 1 if report["summary"]["model_error"] or report["summary"]["input_rejected"] else 0
    except (OSError, UnicodeError, ValueError, KeyError, TypeError):
        print("Load evaluation failed: check cases, model configuration and a new output path. "
              "Private configuration and upstream errors are not printed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
