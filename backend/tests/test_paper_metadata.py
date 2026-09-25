import pytest

from services.paper_metadata import normalize_subject_code, to_paper_fields


@pytest.mark.parametrize("raw,expected", [
    ("aiml 201", "AIML201"), ("cse-432", "CSE432"), (" it402 ", "IT402"), ("CSE_439", "CSE439"),
    ("", None), ("   ", None), (None, None), (401, "401"),
])
def test_normalize_subject_code(raw, expected):
    assert normalize_subject_code(raw) == expected


def test_to_paper_fields_maps_and_cleans():
    fields = to_paper_fields({"subjectCode": "cse 432", "subjectName": " Software Project Management ",
                              "semester": "", "monthYear": "June, 2023", "time": "3 Hrs.", "marks": 60})
    assert fields == {"subject_code": "CSE432", "subject_name": "Software Project Management", "semester": None,
                      "year": "June, 2023", "time": "3 Hrs.", "marks": "60"}


def test_to_paper_fields_handles_marks_dict_and_missing_metadata():
    assert to_paper_fields({"marks": {"Max Marks": "100"}})["marks"] == "100"
    assert to_paper_fields(None) == {k: None for k in ("subject_code", "subject_name", "semester", "year", "time", "marks")}
