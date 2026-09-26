from types import SimpleNamespace

import pytest

from services import rag_service
from services.rag_service import RAGService

HEADER = """--- Page 1 ---
[No. of Printed Pages-4] 1242
CSE432 Enrol. No.
END SEMESTER EXAMINATIONS:
SOFTWARE PROJECT MANAGEMENT
Time: 3 Hrs. Maximum Marks: 60
Note: Attempt questions from all sections as directed.
"""
BODY = """SECTION - A (24 Marks)
Attempt any four questions out of five.
1. Discuss the different types of stakeholders.
P.T.O.
--- Page 2 ---
2. (a) Identify the appropriate SDLC for a defense project. (3)
SECTION - C (16 Marks)
9. Calculate the NPV at 10% discount rate for each project."""


class FakeVectors:
    """Stands in for VectorService; `results` are (score, filename) pairs in Qdrant's score order."""

    results: list = []
    calls: list = []

    def search_similar(self, query, limit, with_payload=True):
        FakeVectors.calls.append((query, limit))
        return [SimpleNamespace(id=i, score=score, payload={"filename": name, "subject_code": "X", "full_text": f"text of {name}"})
                for i, (score, name) in enumerate(FakeVectors.results)]


@pytest.fixture
def rag(monkeypatch):
    FakeVectors.calls = []
    monkeypatch.setattr(rag_service, "VectorService", FakeVectors)
    return RAGService()


def test_retrieval_drops_duplicates_and_other_subjects(rag):
    FakeVectors.results = [(0.472, "SPM 23.pdf"), (0.441, "SPM Minor 2022.pdf"), (0.441, "SPM Minor 2022.pdf"),
                           (0.420, "SPM Major 2025.pdf"), (0.131, "Cloud Sec Major 2023.pdf")]
    papers = rag._retrieve_similar_papers("Software Project Management", limit=3)
    assert [p["filename"] for p in papers] == ["SPM 23.pdf", "SPM Minor 2022.pdf", "SPM Major 2025.pdf"]
    assert FakeVectors.calls == [("Software Project Management", 6)]


def test_retrieval_keeps_a_closely_related_subject_and_respects_the_limit(rag):
    FakeVectors.results = [(0.293, "Intro to AIML 2022.pdf"), (0.293, "Intro to AIML 2022.pdf"),
                           (0.277, "AI Major 2023(1).PDF"), (0.276, "AI Major 2023.pdf"), (0.270, "AI Minor 2024.pdf")]
    papers = rag._retrieve_similar_papers("Artificial Intelligence", limit=3)
    assert [p["filename"] for p in papers] == ["Intro to AIML 2022.pdf", "AI Major 2023(1).PDF", "AI Major 2023.pdf"]


def test_five_papers_are_used_by_default(rag):
    FakeVectors.results = [(0.47 - i * 0.01, f"SPM {i}.pdf") for i in range(8)]
    papers = rag._retrieve_similar_papers("Software Project Management")
    assert [p["filename"] for p in papers] == [f"SPM {i}.pdf" for i in range(5)]
    assert FakeVectors.calls == [("Software Project Management", 10)]


def test_the_stream_retrieves_the_default_number_of_papers(client, auth_headers, monkeypatch):
    seen = []

    class Rag:
        def _retrieve_similar_papers(self, subject, **kwargs):
            seen.append(kwargs)
            return []

    monkeypatch.setattr("api.routes.RAGService", Rag)
    client.post("/api/v1/generate/mock-paper-stream", headers=auth_headers(role="faculty"),
                json={"subject": "SPM", "sections": [{"name": "A", "questions": [{"number": 1}]}]})
    assert seen == [{}]


def test_context_drops_the_exam_header_and_page_markers_but_keeps_every_question(rag):
    context = rag._extract_paper_context([{"filename": "SPM Major 2025.pdf", "ocrText": HEADER + BODY}])
    assert context.startswith("=== SPM Major 2025.pdf ===\nSECTION - A (24 Marks)")
    for kept in ("1. Discuss the different types of stakeholders.", "2. (a) Identify the appropriate SDLC",
                 "SECTION - C (16 Marks)", "9. Calculate the NPV"):
        assert kept in context
    for dropped in ("Time: 3 Hrs", "Maximum Marks", "Enrol. No.", "--- Page", "P.T.O."):
        assert dropped not in context


def test_context_is_not_truncated(rag):
    long_body = "SECTION - A\n" + "\n".join(f"{n}. Question number {n} about a topic." for n in range(1, 800)) + "\nLAST-LINE"
    context = rag._extract_paper_context([{"filename": "long.pdf", "ocrText": long_body},
                                          {"filename": "other.pdf", "ocrText": "SECTION - A\n1. Other paper question."}])
    assert len(context) > 20000
    assert "LAST-LINE" in context and "1. Other paper question." in context


def test_text_without_a_section_or_question_marker_is_kept(rag):
    context = rag._extract_paper_context([{"filename": "odd.pdf", "ocrText": "--- Page 1 ---\nExplain cloud elasticity."}])
    assert context == "=== odd.pdf ===\nExplain cloud elasticity.\n"


def test_question_prompt_sends_all_papers_and_asks_for_grounded_questions(rag):
    context = "=== A.pdf ===\n" + "x" * 5000 + "\nEND-OF-A\n=== B.pdf ===\n1. Define risk.\n"
    prompt = rag._build_question_prompt("Software Project Management", "apply", 10, [], context)
    assert "END-OF-A" in prompt and "1. Define risk." in prompt
    assert "for style only" not in prompt
    assert "PAST PAPER QUESTIONS" in prompt
    assert "what they ask" in prompt and "how they ask it" in prompt
    assert "the way the past papers themselves vary" in prompt
    assert "Do not copy or lightly reword" in prompt


def test_question_prompt_ties_the_topic_to_the_target_module(rag):
    prompt = rag._build_question_prompt("Software Project Management", "remember", 2, [], "ctx", module="Risk Management")
    assert "TARGET MODULE: Risk Management" in prompt
    assert "within the target module" in prompt


def test_batched_prompt_sends_all_papers_and_asks_for_grounded_questions(rag, monkeypatch):
    sent = []

    class Response:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": "[]"}}]}

    def fake_post(url, headers, json, timeout):
        sent.append(json["messages"][0]["content"])
        return Response()

    monkeypatch.setattr(rag_service.requests, "post", fake_post)
    context = "=== A.pdf ===\n" + "y" * 4000 + "\nEND-OF-A\n"
    section = {"name": "Section A", "questions": [{"number": 1, "bloomLevel": "remember", "totalMarks": 2, "parts": []}]}

    rag._generate_section(section, context, "Software Project Management")

    assert "END-OF-A" in sent[0]
    assert "how they ask it" in sent[0]
    assert "Do not copy or lightly reword" in sent[0]
