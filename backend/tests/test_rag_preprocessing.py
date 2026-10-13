"""Synthetic text only. Real PDFs stay outside version control and test logs."""
from copy import deepcopy
import re

import pytest

from backend.rag_preprocessing import preprocess_sections


def section(text, page=7, heading="페이지 7"):
    return {"page": page, "heading": heading, "text": text}


def compact(text):
    return re.sub(r"\s", "", text)


def test_clause_boundaries_page_metadata_and_input_immutability():
    source = [section("1 General\nKeep the record.\n1.1 Fuel\n연료 0.50% and 3.114 units.\n1.1.1 Checks\nVerify it."),
              section("Next physical page.", page=8)]
    original = deepcopy(source)
    chunks = preprocess_sections(source)
    assert [c["page"] for c in chunks] == [7, 7, 7, 8]
    assert [c["heading"] for c in chunks[:3]] == ["1 General", "1.1 Fuel", "1.1.1 Checks"]
    assert compact("".join(c["text"] for c in chunks)) == compact("".join(s["text"] for s in source))
    assert source == original


def test_long_text_uses_sentences_without_losing_punctuation_or_decimals():
    text = "1.1 Fuel\n" + "Fuel limit is 0.50%. 연료 기록을 확인한다. " * 80
    chunks = preprocess_sections([section(text)])
    assert len(chunks) > 2
    assert all(0 < len(c["text"]) <= 900 for c in chunks)
    assert all(c["text"].endswith(".") for c in chunks)
    assert compact("".join(c["text"] for c in chunks)) == compact(text)
    assert all(c["heading"] == "1.1 Fuel" for c in chunks)


def test_paragraph_boundary_precedes_sentence_and_no_overlap():
    first = "A complete fictional requirement. " * 17
    second = "A different fictional requirement. " * 20
    chunks = preprocess_sections([section(first + "\n\n" + second)])
    assert chunks[0]["text"] == first.strip()
    assert compact("".join(c["text"] for c in chunks)) == compact(first + second)


def test_table_rows_numbers_and_captions_survive():
    rows = [f"PC{i}   0.50   3.114   17.69 (1804, 1794)" for i in range(1, 45)]
    text = "TABLE 1\nFictional class factors\n" + "\n".join(rows) + "\nNote: values are synthetic."
    chunks = preprocess_sections([section(text)])
    assert all(c["heading"] == "TABLE 1" for c in chunks)
    assert all(len(c["text"]) <= 900 for c in chunks)
    for row in rows:
        assert sum(row in c["text"] for c in chunks) == 1
    assert compact("".join(c["text"] for c in chunks)) == compact(text)


def test_only_repeated_recognized_edges_are_removed():
    header = "Part   6   Synthetic Manual"
    footer = "ABS RULES FOR BUILDING AND CLASSING SYNTHETIC VESSELS • 2026"
    source = [section(f"{header}\nChapter 1 Synthetic Chapter\nSection 2 Synthetic Section\n"
                      f"1 General\nRepeated safety requirement.\n{footer} {i}", i) for i in [1, 2, 3]]
    chunks = preprocess_sections(source)
    assert len(chunks) == 3
    assert all(c["text"] == "1 General\nRepeated safety requirement." for c in chunks)
    assert all("Section 2 Synthetic Section" in c["heading"] for c in chunks)
    assert [c["page"] for c in chunks] == [1, 2, 3]
    # Repeated chunks on ONE page, or fewer than three pages, do not qualify.
    assert header in preprocess_sections(source[:2])[0]["text"]
    same_page = [{**s, "page": 1} for s in source]
    assert header in preprocess_sections(same_page)[0]["text"]
    # The same running-header text inside the body must remain.
    middle = section("Intro\nSecond\nThird\nFourth\n" + header + "\nEnd", 4)
    assert header in preprocess_sections(source + [middle])[-1]["text"]


def test_blank_unknown_page_and_line_endings():
    chunks = preprocess_sections([section(" \r\n\t"), section("본문\r\n연료\u00a0기록", None, "제1조")])
    assert chunks == [{"page": None, "heading": "제1조", "text": "본문\n연료 기록"}]


@pytest.mark.parametrize("text", ["가" * 2300, "x" * 2300, "한영 mixed " * 230, "0.50 " * 460])
def test_overlong_strings_are_bounded_and_reconstructable(text):
    chunks = preprocess_sections([section(text)])
    assert all(0 < len(c["text"]) <= 900 for c in chunks)
    assert compact("".join(c["text"] for c in chunks)) == compact(text)


def test_toc_and_bullets_are_not_promoted_to_headings():
    text = "SECTION 3 Safety........12\n1 General.......13\n1. Keep this sentence.\ni) Check units.\n● Check equipment."
    assert preprocess_sections([section(text)])[0]["text"] == text
    assert preprocess_sections([section(text)])[0]["heading"] == "페이지 7"


def test_repeated_processing_is_deterministic_and_does_not_join_pages():
    source = [section("Continuation", 1), section("of a rule.", 2)]
    assert preprocess_sections(source) == preprocess_sections(source)
    assert len(preprocess_sections(source)) == 2


def test_heading_length_stays_within_existing_api_contract():
    result = preprocess_sections([section("1.1 Objective\nA requirement.", heading="가" * 200)])
    assert len(result[0]["heading"]) <= 200
    assert result[0]["heading"].endswith("1.1 Objective")


@pytest.mark.parametrize("limit", [0, -1, 10, True, 99.5])
def test_invalid_limit_rejected(limit):
    with pytest.raises(ValueError):
        preprocess_sections([], limit)
