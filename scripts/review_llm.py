"""Prepare and summarize human reviews of saved fixed-evidence evaluations.

This offline tool never calls a model or reads its configuration. Human verdicts
are recorded separately from citation checks and from the original report.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import evaluate_llm as evaluation

DEFAULT_CASES = evaluation.DEFAULT_CASES
DEFAULT_SEED = evaluation.DEFAULT_SEED
SCOPE = "fixed_seed_evidence_without_retrieval_auth_or_browser"
VERDICTS = {"pending", "pass", "fail"}
REVIEW_KEYS = {"id", "input", "result", "verdict", "reviewer", "reviewed_at", "notes"}


def _nonblank(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _fingerprint(cases: list[dict]) -> str:
    return hashlib.sha256(json.dumps(
        cases, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def _hash(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _same_json(left: object, right: object) -> bool:
    # Python equality treats True as 1; saved snapshots must preserve JSON types.
    return json.dumps(left, sort_keys=True, ensure_ascii=False) == json.dumps(
        right, sort_keys=True, ensure_ascii=False)


def _read_report(path: Path) -> tuple[dict, str]:
    raw = Path(path).read_bytes()
    report = json.loads(raw.decode("utf-8"))
    if (not isinstance(report, dict) or type(report.get("schema_version")) is not int
            or report["schema_version"] != 1 or report.get("evaluation_scope") != SCOPE
            or report.get("semantic_review") != "pending"
            or not _hash(report.get("llm_code_sha256"))
            or not _hash(report.get("evaluation_input_sha256"))
            or not isinstance(report.get("results"), list) or not report["results"]):
        raise ValueError("Invalid evaluation report")
    mode = report.get("mode")
    if (not isinstance(mode, str) or mode not in {"llm", "extractive"}
            or report.get("execution") != ("real_model" if mode == "llm" else "extractive")
            or report.get("model_call_attempted") is not (mode == "llm")
            or (mode == "llm" and (not _nonblank(report.get("model"))
                                    or not isinstance(report.get("response_format"), str)
                                    or report.get("response_format") not in
                                    {"text", "json_object", "json_schema"}))
            or (mode == "extractive" and (report.get("model") is not None
                                          or report.get("response_format") is not None))):
        raise ValueError("Inconsistent execution metadata")
    if not isinstance(report.get("started_at"), str):
        raise ValueError("Invalid execution date")
    started = datetime.fromisoformat(report["started_at"])
    if started.tzinfo is None:
        raise ValueError("Execution date requires timezone")
    ids = [row.get("id") if isinstance(row, dict) else None for row in report["results"]]
    if any(not _nonblank(value) for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("Invalid or duplicate case IDs")
    counts = {"cases": len(ids), **{key: sum(row.get("outcome") == key for row in report["results"])
                                  for key in ("answered", "insufficient", "model_error")}}
    if (report.get("summary") != counts
            or not isinstance(report["summary"], dict)
            or any(type(value) is not int for value in report["summary"].values())
            or sum(counts[key] for key in ("answered", "insufficient", "model_error")) != len(ids)):
        raise ValueError("Inconsistent evaluation counts")
    return report, hashlib.sha256(raw).hexdigest()


def _validate_run(report: dict, cases: list[dict]) -> None:
    """Bind source snapshots and technical output to their original input."""
    if (not isinstance(cases, list) or not cases
            or any(not isinstance(case, dict) for case in cases)
            or [case.get("id") for case in cases] != [row["id"] for row in report["results"]]
            or _fingerprint(cases) != report["evaluation_input_sha256"]):
        raise ValueError("Evaluation input differs from saved sources")
    for case, row in zip(cases, report["results"]):
        evidence = case.get("evidence")
        if (not isinstance(evidence, list) or not evidence
                or any(not isinstance(item, dict) for item in evidence)
                or not isinstance(case.get("expected"), dict)
                or type(case["expected"].get("insufficient")) is not bool
                or row.get("question") != case.get("question")
                or row.get("expected") != case["expected"]):
            raise ValueError("Invalid saved question or expected facts")
        sources = [{key: item[key] for key in ("id", "document_id", "title", "heading", "page")}
                   for item in evidence]
        mode, outcome = report["mode"], row.get("outcome")
        duration = row.get("duration_seconds")
        if (row.get("sources") != sources or row.get("semantic_review") != "pending"
                or row.get("model_call_attempted") is not (mode == "llm")
                or not isinstance(duration, (int, float)) or isinstance(duration, bool)
                or not math.isfinite(duration) or duration < 0
                or not isinstance(row.get("statements"), list)):
            raise ValueError("Invalid saved technical output")
        statements = row["statements"]
        expected_match = outcome == ("insufficient" if case["expected"]["insufficient"] else "answered")
        if row.get("expected_insufficient_matches") is not expected_match:
            raise ValueError("Inconsistent expected insufficiency check")
        if outcome == "model_error":
            if (mode != "llm" or statements or row.get("generation") is not None
                    or row.get("citation_valid") is not False or not _nonblank(row.get("error_type"))):
                raise ValueError("Invalid model error result")
        else:
            if (outcome not in {"answered", "insufficient"}
                    or row.get("error_type") is not None or row.get("generation") != mode
                    or (outcome == "answered" and (not statements or row.get("citation_valid") is not True))
                    or (outcome == "insufficient" and (statements or row.get("citation_valid") is not None))
                    or (mode == "extractive" and outcome != "answered")):
                raise ValueError("Inconsistent saved generation")
            evaluation.llm.validate_statements({"insufficient": outcome == "insufficient",
                                                "statements": statements}, evidence)
        contains_hangul = bool(re.search(r"[가-힣]", " ".join(item["text"] for item in statements)))
        if row.get("contains_hangul") is not contains_hangul:
            raise ValueError("Inconsistent language check")


def prepare(report_path: Path, *, case_path: Path = DEFAULT_CASES,
            seed_path: Path = DEFAULT_SEED) -> dict:
    """Build a pending worksheet, validating original fixed inputs first."""
    report, report_sha256 = _read_report(report_path)
    available = evaluation.load_cases(Path(case_path), Path(seed_path))
    ids = {row["id"] for row in report["results"]}
    cases = [case for case in available if case["id"] in ids]
    _validate_run(report, cases)
    return {"schema_version": 1, "report_sha256": report_sha256,
            "report_metadata": deepcopy({key: value for key, value in report.items() if key != "results"}),
            "cases": [{"id": case["id"], "input": deepcopy(case), "result": deepcopy(row),
                       "verdict": "pending", "reviewer": "", "reviewed_at": "", "notes": ""}
                      for case, row in zip(cases, report["results"])]}


def summarize(report_path: Path, review_path: Path) -> dict:
    """Count explicit human verdicts without inferring semantic correctness."""
    report, report_sha256 = _read_report(report_path)
    review = json.loads(Path(review_path).read_text(encoding="utf-8"))
    metadata = {key: value for key, value in report.items() if key != "results"}
    if (not isinstance(review, dict)
            or set(review) != {"schema_version", "report_sha256", "report_metadata", "cases"}
            or type(review.get("schema_version")) is not int or review["schema_version"] != 1
            or review.get("report_sha256") != report_sha256
            or not _same_json(review.get("report_metadata"), metadata)
            or not isinstance(review.get("cases"), list)
            or len(review["cases"]) != len(report["results"])):
        raise ValueError("Review does not match original report")
    entries = review["cases"]
    if any(not isinstance(entry, dict) or set(entry) != REVIEW_KEYS for entry in entries):
        raise ValueError("Invalid review entry")
    if [entry["id"] for entry in entries] != [row["id"] for row in report["results"]]:
        raise ValueError("Missing, duplicate, unknown or reordered review IDs")
    if any(not _same_json(entry["result"], row) for entry, row in zip(entries, report["results"])):
        raise ValueError("Saved evaluation results were changed")
    _validate_run(report, [entry["input"] for entry in entries])
    for entry in entries:
        verdict = entry["verdict"]
        if (not isinstance(verdict, str) or verdict not in VERDICTS
                or any(not isinstance(entry[key], str) for key in ("reviewer", "reviewed_at", "notes"))):
            raise ValueError("Invalid human verdict")
        if verdict != "pending":
            if (not _nonblank(entry["reviewer"]) or not _nonblank(entry["notes"])
                    or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", entry["reviewed_at"]) is None):
                raise ValueError("Completed verdict requires reviewer, date and notes")
            date.fromisoformat(entry["reviewed_at"])
        elif entry["reviewed_at"]:
            if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", entry["reviewed_at"]) is None:
                raise ValueError("Invalid review date")
            date.fromisoformat(entry["reviewed_at"])
        if entry["result"]["outcome"] == "model_error" and verdict == "pass":
            raise ValueError("A model error cannot pass human quality review")
    counts = {"cases": len(entries), **{key: sum(entry["verdict"] == key for entry in entries)
                                       for key in ("pass", "fail", "pending")},
              "model_error": report["summary"]["model_error"]}
    if report["mode"] == "extractive":
        status = "extractive"
    elif counts["model_error"] or counts["fail"]:
        status = "fail"
    elif counts["pending"]:
        status = "pending"
    else:
        status = "pass"
    return {"schema_version": 1, "report_sha256": report_sha256,
            "report_metadata": deepcopy(metadata), "status": status, "summary": counts,
            "cases": [{key: entry[key] for key in ("id", "verdict", "reviewer", "reviewed_at", "notes")}
                      | {"outcome": entry["result"]["outcome"]} for entry in entries]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("prepare", help="Create a pending human review worksheet")
    create.add_argument("--report", required=True, type=Path)
    create.add_argument("--output", required=True, type=Path)
    create.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    create.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    summary = commands.add_parser("summarize", help="Validate and summarize saved human verdicts")
    summary.add_argument("--report", required=True, type=Path)
    summary.add_argument("--review", required=True, type=Path)
    summary.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = (prepare(args.report, case_path=args.cases, seed_path=args.seed)
                  if args.command == "prepare" else summarize(args.report, args.review))
        serialized = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output is None:
            sys.stdout.write(serialized)
        else:
            with args.output.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(serialized)
        return 0
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, AttributeError, OverflowError):
        print("Review failed: check report integrity, matching input snapshots, human verdicts "
              "and a new output path.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
