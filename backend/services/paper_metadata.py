import re

PAPER_FIELDS = ("subject_code", "subject_name", "semester", "year", "time", "marks")


def normalize_subject_code(code) -> str | None:
    """'aiml 201' -> 'AIML201': uppercase, no spaces, hyphens or underscores."""
    if code is None:
        return None
    cleaned = re.sub(r"[\s\-_]+", "", str(code)).upper()
    return cleaned or None


def _text(value) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _marks(value) -> str | None:
    # The LLM sometimes returns marks as a dict or an int
    if isinstance(value, dict):
        value = value.get("Max Marks") or value.get("max_marks") or str(value)
    return _text(value)


def to_paper_fields(metadata: dict | None) -> dict[str, str | None]:
    """Maps LLM metadata keys to `papers` columns, normalizing the subject code."""
    m = metadata or {}
    return {
        "subject_code": normalize_subject_code(m.get("subjectCode") or m.get("Subject Code")),
        "subject_name": _text(m.get("subjectName") or m.get("Subject Name")),
        "semester": _text(m.get("semester")),
        "year": _text(m.get("monthYear") or m.get("Month/Year")),
        "time": _text(m.get("time")),
        "marks": _marks(m.get("marks")),
    }
