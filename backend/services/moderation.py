"""Automatic checks run on every upload. Any flag holds the paper for an admin; none means it goes live.

Thresholds were measured on the 14 production papers (2026-09-26): the smallest real paper has 806
readable letters; every paper mentions marks and time and uses at least 4 distinct exam words; re-scans
of one paper overlap 0.83-1.00, different papers at most 0.07.
"""
import re

from services.rag_service import RAGService

MIN_READABLE_LETTERS = 300
DUPLICATE_OVERLAP = 0.5
DUPLICATE_CANDIDATES = 5
REQUIRED_DETAILS = ("subject_code", "subject_name", "year")

_NOISE_LINE = re.compile(r"^(--- Page \d+ ---|\[OCR Error\])$")
_MARKS = re.compile(r"\bmarks?\b", re.IGNORECASE)
_TIME = re.compile(r"\btime\b|\b\d+(\.\d+)?\s*(hrs?|hours?)\b", re.IGNORECASE)
_EXAM_WORDS = re.compile(r"\b(section|attempt|answer|compulsory|examination|semester)\b", re.IGNORECASE)
_WORD = re.compile(r"[a-z0-9]+")


def readable_letters(text: str) -> int:
    """Letters in the OCR text, ignoring the page markers and OCR error lines the pipeline adds."""
    lines = [line for line in (text or "").splitlines() if not _NOISE_LINE.match(line.strip())]
    return sum(ch.isalpha() for ch in "\n".join(lines))


def looks_like_exam_paper(text: str) -> bool:
    """Mentions marks and a time limit, and uses at least two different exam words."""
    text = text or ""
    exam_words = {word.lower() for word in _EXAM_WORDS.findall(text)}
    return bool(_MARKS.search(text)) and bool(_TIME.search(text)) and len(exam_words) >= 2


def missing_details(fields: dict) -> list[str]:
    return [key for key in REQUIRED_DETAILS if not (fields.get(key) or "").strip()]


def shingles(text: str) -> set[str]:
    """3-word shingles of the question text (the exam header is dropped: it's the same on every paper)."""
    words = _WORD.findall(RAGService._questions_only(text or "").lower())
    return {" ".join(words[i:i + 3]) for i in range(len(words) - 2)}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def text_overlap(a: str, b: str) -> float:
    return jaccard(shingles(a), shingles(b))


def review_reasons(text: str, fields: dict, candidates: list[tuple[int, str]]) -> list[dict]:
    """Flags for the review queue; an empty list means the paper can go live.

    `candidates` are (paper_id, stored_text) pairs for the nearest stored papers.
    """
    reasons: list[dict] = []
    letters = readable_letters(text)
    if letters < MIN_READABLE_LETTERS:
        reasons.append({"code": "unreadable", "letters": letters})
    else:
        if not looks_like_exam_paper(text):
            reasons.append({"code": "not_exam_paper"})
        copies = []
        for paper_id, other_text in candidates:
            overlap = text_overlap(text, other_text)
            if overlap >= DUPLICATE_OVERLAP:
                copies.append({"code": "possible_duplicate", "paperId": paper_id, "overlap": round(overlap, 2)})
        reasons.extend(sorted(copies, key=lambda reason: -reason["overlap"]))
    missing = missing_details(fields)
    if missing:
        reasons.append({"code": "missing_details", "fields": missing})
    return reasons
