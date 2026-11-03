"""Human-review workflow checks; model outputs are mocked, never real calls."""

from copy import deepcopy
import hashlib
import json

import pytest

from api import config, llm
from scripts import evaluate_llm as evaluation
from scripts import review_llm as review


SETTINGS = config.LLMSettings("http://localhost:11434/v1", "mock-model", "", "json_schema")


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def report_file(tmp_path, monkeypatch, *, ids=("sulphur-limits",), mode="llm",
                outcome="answered", answer=None):
    """Create reports with the production evaluator and an explicitly mocked model."""
    selected = [case for case in evaluation.load_cases() if case["id"] in ids]

    def mocked_model(_question, evidence, _language, _settings):
        if outcome == "model_error":
            raise ValueError("mock invalid model output")
        if outcome == "insufficient":
            return []
        source = evidence[0]
        return [{"text": source["text"] if answer is None else answer,
                 "chunkId": source["id"], "quote": source["text"]}]

    monkeypatch.setattr(llm, "grounded_statements", mocked_model)
    report = evaluation.evaluate(selected, mode, settings=SETTINGS if mode == "llm" else None)
    return write_json(tmp_path / "evaluation.json", report), report


def completed(worksheet, verdict="pass"):
    result = deepcopy(worksheet)
    for case in result["cases"]:
        case.update(verdict=verdict, reviewer="test-reviewer", reviewed_at="2026-11-03",
                    notes="Test-only explicit human decision; not a real model quality result.")
    return result


def summarize(tmp_path, report_path, worksheet):
    review_path = write_json(tmp_path / "review.json", worksheet)
    return review.summarize(report_path, review_path)


def test_valid_citation_wrong_answer_starts_pending_and_human_can_fail(tmp_path, monkeypatch):
    report_path, report = report_file(tmp_path, monkeypatch, answer="문서의 황 한도는 99%입니다.")
    assert report["results"][0]["citation_valid"] is True
    worksheet = review.prepare(report_path)
    row = worksheet["cases"][0]
    assert row["verdict"] == "pending"
    assert "99%" in row["result"]["statements"][0]["text"]
    assert row["input"]["expected"]["facts"]
    assert row["input"]["evidence"][0]["text"]
    assert worksheet["report_sha256"] == hashlib.sha256(report_path.read_bytes()).hexdigest()
    pending = summarize(tmp_path, report_path, worksheet)
    assert pending["status"] == "pending"
    assert pending["summary"]["pending"] == 1
    failed = summarize(tmp_path, report_path, completed(worksheet, "fail"))
    assert failed["status"] == "fail"
    assert failed["summary"]["fail"] == 1 and failed["summary"]["pass"] == 0


def test_all_explicit_reviews_pass_with_original_provenance(tmp_path, monkeypatch):
    report_path, report = report_file(tmp_path, monkeypatch, ids=("sulphur-limits", "cii-scope"))
    worksheet = completed(review.prepare(report_path))
    summary = summarize(tmp_path, report_path, worksheet)
    assert summary["status"] == "pass"
    assert summary["summary"] == {"cases": 2, "pass": 2, "fail": 0, "pending": 0, "model_error": 0}
    assert summary["report_metadata"]["model"] == "mock-model"
    assert summary["report_metadata"]["evaluation_input_sha256"] == report["evaluation_input_sha256"]
    assert all(row["reviewer"] == "test-reviewer" for row in summary["cases"])


def test_one_pending_review_prevents_overall_pass(tmp_path, monkeypatch):
    report_path, _ = report_file(tmp_path, monkeypatch, ids=("sulphur-limits", "cii-scope"))
    worksheet = review.prepare(report_path)
    worksheet["cases"][0].update(completed(worksheet)["cases"][0])
    summary = summarize(tmp_path, report_path, worksheet)
    assert summary["status"] == "pending"
    assert summary["summary"]["pass"] == 1 and summary["summary"]["pending"] == 1


def test_extractive_reviews_never_claim_actual_model_quality_pass(tmp_path, monkeypatch):
    report_path, _ = report_file(tmp_path, monkeypatch, mode="extractive")
    summary = summarize(tmp_path, report_path, completed(review.prepare(report_path)))
    assert summary["status"] == "extractive"
    assert summary["report_metadata"]["model_call_attempted"] is False
    assert summary["report_metadata"]["execution"] == "extractive"


def test_expected_insufficiency_can_be_reviewed_without_fabricated_answer(tmp_path, monkeypatch):
    report_path, _ = report_file(tmp_path, monkeypatch, ids=("unsupported-captain-name",),
                                 outcome="insufficient")
    worksheet = review.prepare(report_path)
    assert worksheet["cases"][0]["result"]["statements"] == []
    assert worksheet["cases"][0]["input"]["expected"]["insufficient"] is True
    assert summarize(tmp_path, report_path, completed(worksheet))["status"] == "pass"


def test_model_error_is_separate_failure_and_cannot_be_marked_pass(tmp_path, monkeypatch):
    report_path, _ = report_file(tmp_path, monkeypatch, outcome="model_error")
    worksheet = review.prepare(report_path)
    summary = summarize(tmp_path, report_path, worksheet)
    assert summary["status"] == "fail"
    assert summary["summary"]["model_error"] == 1
    assert summary["summary"]["pass"] == 0
    with pytest.raises(ValueError):
        summarize(tmp_path, report_path, completed(worksheet))


@pytest.mark.parametrize("field,value", [
    ("reviewer", ""), ("reviewer", "   "), ("reviewed_at", ""),
    ("reviewed_at", "2026-02-30"), ("reviewed_at", "2026-11-03T09:00:00"),
    ("notes", ""), ("notes", "   "), ("verdict", "approved"),
])
def test_completed_review_requires_valid_metadata(tmp_path, monkeypatch, field, value):
    report_path, _ = report_file(tmp_path, monkeypatch)
    worksheet = completed(review.prepare(report_path))
    worksheet["cases"][0][field] = value
    with pytest.raises(ValueError):
        summarize(tmp_path, report_path, worksheet)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unknown", "reordered"])
def test_missing_duplicate_unknown_or_reordered_ids_rejected(tmp_path, monkeypatch, mutation):
    report_path, _ = report_file(tmp_path, monkeypatch, ids=("sulphur-limits", "cii-scope"))
    worksheet = review.prepare(report_path)
    if mutation == "missing":
        worksheet["cases"].pop()
    elif mutation == "duplicate":
        worksheet["cases"][1]["id"] = worksheet["cases"][0]["id"]
    elif mutation == "unknown":
        worksheet["cases"][0]["id"] = "not-in-this-report"
    else:
        worksheet["cases"].reverse()
    with pytest.raises(ValueError):
        summarize(tmp_path, report_path, worksheet)


def test_report_byte_changes_reject_existing_review_even_with_same_json(tmp_path, monkeypatch):
    report_path, _ = report_file(tmp_path, monkeypatch)
    worksheet = review.prepare(report_path)
    report_path.write_bytes(report_path.read_bytes() + b"\n")
    with pytest.raises(ValueError):
        summarize(tmp_path, report_path, worksheet)


@pytest.mark.parametrize("mutation", ["metadata", "question", "evidence", "actual_answer"])
def test_saved_input_or_output_context_cannot_be_silently_changed(tmp_path, monkeypatch, mutation):
    report_path, _ = report_file(tmp_path, monkeypatch)
    worksheet = review.prepare(report_path)
    if mutation == "metadata":
        worksheet["report_metadata"]["model"] = "different-model"
    elif mutation == "question":
        worksheet["cases"][0]["input"]["question"] += " edited"
    elif mutation == "evidence":
        worksheet["cases"][0]["input"]["evidence"][0]["text"] += " edited"
    else:
        worksheet["cases"][0]["result"]["statements"][0]["text"] += " edited"
    with pytest.raises(ValueError):
        summarize(tmp_path, report_path, worksheet)


@pytest.mark.parametrize("snapshot", ["metadata", "result"])
def test_json_boolean_cannot_be_replaced_by_python_equal_integer(tmp_path, monkeypatch, snapshot):
    report_path, _ = report_file(tmp_path, monkeypatch)
    worksheet = review.prepare(report_path)
    target = (worksheet["report_metadata"] if snapshot == "metadata"
              else worksheet["cases"][0]["result"])
    assert target["model_call_attempted"] is True
    target["model_call_attempted"] = 1
    with pytest.raises(ValueError):
        summarize(tmp_path, report_path, worksheet)


def test_current_fixture_drift_rejected_but_matching_historical_files_supported(tmp_path, monkeypatch):
    report_path, _ = report_file(tmp_path, monkeypatch)
    original = json.loads(evaluation.DEFAULT_CASES.read_text(encoding="utf-8"))
    historical_cases = write_json(tmp_path / "historical-cases.json", original)
    original[0]["question"] += " drift"
    changed_cases = write_json(tmp_path / "changed-cases.json", original)
    with pytest.raises(ValueError):
        review.prepare(report_path, case_path=changed_cases)
    worksheet = review.prepare(report_path, case_path=historical_cases)
    assert worksheet["cases"][0]["id"] == "sulphur-limits"


def test_historical_calling_code_hash_is_preserved_not_compared_to_current_code(tmp_path, monkeypatch):
    report_path, report = report_file(tmp_path, monkeypatch)
    report["llm_code_sha256"] = "0" * 64
    write_json(report_path, report)
    worksheet = review.prepare(report_path)
    assert worksheet["report_metadata"]["llm_code_sha256"] == "0" * 64
    assert summarize(tmp_path, report_path, completed(worksheet))["status"] == "pass"


def test_historical_schema_one_long_statement_remains_reviewable_under_new_runtime_caps(tmp_path, monkeypatch):
    report_path, report = report_file(tmp_path, monkeypatch)
    report["llm_code_sha256"] = "0" * 64
    report["results"][0]["statements"][0]["text"] = "가" * 5000
    report["results"][0]["contains_hangul"] = True
    selected = [case for case in evaluation.load_cases() if case["id"] == "sulphur-limits"]
    with pytest.raises(ValueError):
        llm.validate_statements({"insufficient": False, "statements": report["results"][0]["statements"]},
                                selected[0]["evidence"])
    write_json(report_path, report)

    def current_runtime_validator_is_not_a_saved_schema(*_args, **_kwargs):
        raise AssertionError("Historical schema-1 reviews must not inherit newer runtime limits")

    monkeypatch.setattr(llm, "validate_statements", current_runtime_validator_is_not_a_saved_schema)
    worksheet = review.prepare(report_path)
    assert len(worksheet["cases"][0]["result"]["statements"][0]["text"]) == 5000
    assert worksheet["cases"][0]["verdict"] == "pending"
    assert summarize(tmp_path, report_path, worksheet)["status"] == "pending"


def test_saved_review_summary_does_not_depend_on_current_fixture_files(tmp_path, monkeypatch):
    report_path, _ = report_file(tmp_path, monkeypatch)
    worksheet = review.prepare(report_path)

    def unavailable(*_args, **_kwargs):
        raise AssertionError("Summary must use hash-checked saved source snapshots")

    monkeypatch.setattr(evaluation, "load_cases", unavailable)
    assert summarize(tmp_path, report_path, completed(worksheet))["status"] == "pass"


def test_prepare_and_summarize_do_not_read_model_configuration_or_call_network(tmp_path, monkeypatch):
    report_path, _ = report_file(tmp_path, monkeypatch)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Saved review workflow must stay offline")

    monkeypatch.setattr(config, "llm_settings", forbidden)
    monkeypatch.setattr(llm, "grounded_statements", forbidden)
    monkeypatch.setattr(llm.httpx, "post", forbidden)
    worksheet = review.prepare(report_path)
    assert summarize(tmp_path, report_path, worksheet)["status"] == "pending"


def test_cli_utf8_round_trip_and_existing_outputs_are_preserved(tmp_path, monkeypatch, capsys):
    report_path, _ = report_file(tmp_path, monkeypatch)
    output = tmp_path / "한국어-검토.json"
    original_bytes = report_path.read_bytes()
    assert review.main(["prepare", "--report", str(report_path), "--output", str(output)]) == 0
    assert "황" in output.read_text(encoding="utf-8")
    worksheet_bytes = output.read_bytes()
    assert review.main(["prepare", "--report", str(report_path), "--output", str(output)]) == 1
    assert output.read_bytes() == worksheet_bytes
    summary_path = tmp_path / "summary.json"
    assert review.main(["summarize", "--report", str(report_path), "--review", str(output),
                        "--output", str(summary_path)]) == 0
    summary_bytes = summary_path.read_bytes()
    assert json.loads(summary_bytes)["status"] == "pending"
    assert review.main(["summarize", "--report", str(report_path), "--review", str(output),
                        "--output", str(summary_path)]) == 1
    assert summary_path.read_bytes() == summary_bytes
    assert report_path.read_bytes() == original_bytes
    assert "Traceback" not in capsys.readouterr().err


def test_cli_summarizes_pending_to_stdout_and_reports_integrity_failure(tmp_path, monkeypatch, capsys):
    report_path, _ = report_file(tmp_path, monkeypatch)
    review_path = write_json(tmp_path / "review.json", review.prepare(report_path))
    args = ["summarize", "--report", str(report_path), "--review", str(review_path)]
    assert review.main(args) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pending"
    report_path.write_bytes(report_path.read_bytes() + b"\n")
    assert review.main(args) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err
