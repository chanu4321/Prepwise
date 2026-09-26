import pytest

from services.moderation import (DUPLICATE_OVERLAP, MIN_READABLE_LETTERS, looks_like_exam_paper, missing_details,
                                 readable_letters, review_reasons, text_overlap)
from tests.fakes import moderation_fixture

AI = moderation_fixture("ai_major_2023")
AI_RESCAN = moderation_fixture("ai_major_2023_rescan")
CLOUD_A = moderation_fixture("cloud_sec_major_2023_set_a")
CLOUD_B = moderation_fixture("cloud_sec_major_2023_set_b")
FIELDS = {"subject_code": "CSE401", "subject_name": "Artificial Intelligence", "semester": None,
          "year": "April-May, 2023", "time": "3 Hrs", "marks": "60"}


def test_a_rescan_of_the_same_paper_overlaps_heavily():
    assert text_overlap(AI, AI_RESCAN) == pytest.approx(0.825, abs=0.01)
    assert text_overlap(AI, AI) == 1.0


def test_different_papers_from_the_same_session_barely_overlap():
    assert text_overlap(CLOUD_A, CLOUD_B) < 0.05
    assert text_overlap(AI, CLOUD_B) < 0.05


def test_overlap_of_empty_text_is_zero():
    assert text_overlap("", AI) == 0.0


def test_real_papers_look_like_exam_papers():
    for text in (AI, AI_RESCAN, CLOUD_A, CLOUD_B):
        assert looks_like_exam_paper(text)
        assert readable_letters(text) > MIN_READABLE_LETTERS


@pytest.mark.parametrize("text", [
    "Lecture 4 notes. Section 1 covers heuristics. Answer the reading questions. Time to revise.",  # no marks
    "Maximum Marks 60. Section A. Attempt all questions.",  # no time
    "Time: 3 Hrs. Maximum Marks: 60. Section A, Section B, Section C.",  # only one kind of exam word
])
def test_text_missing_an_exam_marker_is_not_an_exam_paper(text):
    assert not looks_like_exam_paper(text)


def test_time_can_be_written_as_hours():
    assert looks_like_exam_paper("01 Hr. Max marks 30. Section A. Attempt any two.")


def test_page_markers_and_ocr_errors_are_not_readable_text():
    assert readable_letters("--- Page 1 ---\n[OCR Error]\n--- Page 2 ---\nabc 12") == 3


def test_missing_details_lists_empty_required_fields():
    assert missing_details(FIELDS) == []
    assert missing_details({**FIELDS, "subject_code": None, "year": "  "}) == ["subject_code", "year"]


def test_clean_paper_has_no_reasons():
    assert review_reasons(AI, FIELDS, [(9, CLOUD_B)]) == []


def test_likely_copy_is_flagged_with_the_matching_paper():
    assert review_reasons(AI_RESCAN, FIELDS, [(9, CLOUD_B), (3, AI)]) == [
        {"code": "possible_duplicate", "paperId": 3, "overlap": 0.83}]


def test_copies_are_listed_closest_first():
    reasons = review_reasons(AI, FIELDS, [(2, AI_RESCAN), (4, AI)])
    assert [r["paperId"] for r in reasons] == [4, 2]


def test_overlap_threshold_is_inclusive(monkeypatch):
    monkeypatch.setattr("services.moderation.text_overlap", lambda a, b: DUPLICATE_OVERLAP)
    assert review_reasons(AI, FIELDS, [(5, "x")]) == [{"code": "possible_duplicate", "paperId": 5, "overlap": 0.5}]
    monkeypatch.setattr("services.moderation.text_overlap", lambda a, b: DUPLICATE_OVERLAP - 0.01)
    assert review_reasons(AI, FIELDS, [(5, "x")]) == []


def test_unreadable_text_skips_the_content_checks_but_still_reports_missing_details():
    reasons = review_reasons("--- Page 1 ---\nblurry", {**FIELDS, "year": None}, [(3, "--- Page 1 ---\nblurry")])
    assert reasons == [{"code": "unreadable", "letters": 6}, {"code": "missing_details", "fields": ["year"]}]


def test_non_exam_text_is_flagged():
    notes = "These are my lecture notes about search algorithms. " * 10
    assert review_reasons(notes, FIELDS, []) == [{"code": "not_exam_paper"}]
