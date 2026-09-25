import json

import pytest

from tests.fakes import FakeRag

BODY = {"subject": "Software Project Management",
        "sections": [{"name": "Section A", "questions": [{"number": 1, "bloomLevel": "remember", "totalMarks": 6, "parts": []}]}]}
STREAM = "/api/v1/generate/mock-paper-stream"
PLAIN = "/api/v1/generate/mock-paper"


@pytest.fixture(autouse=True)
def fake_rag(monkeypatch):
    class Rag(FakeRag):
        papers = list(FakeRag.papers)
        question_error = False
        raise_on_generate = False

    monkeypatch.setattr("api.routes.RAGService", Rag)
    return Rag


def events(response):
    return [json.loads(chunk[6:]) for chunk in response.text.split("\n\n") if chunk.startswith("data: ")]


@pytest.mark.parametrize("url", [STREAM, PLAIN])
def test_anonymous_gets_401(client, store, url):
    assert client.post(url, json=BODY).status_code == 401


@pytest.mark.parametrize("url", [STREAM, PLAIN])
def test_student_gets_403(client, auth_headers, url):
    response = client.post(url, json=BODY, headers=auth_headers(role="student"))
    assert response.status_code == 403 and response.json()["code"] == "forbidden"


def test_user_without_role_is_asked_to_choose(client, auth_headers):
    assert client.post(STREAM, json=BODY, headers=auth_headers()).json()["code"] == "role_required"


def test_trial_faculty_gets_three_then_429(client, auth_headers):
    headers = auth_headers(role="faculty")
    for _ in range(3):
        assert client.post(STREAM, json=BODY, headers=headers).status_code == 200
    response = client.post(STREAM, json=BODY, headers=headers)
    assert response.status_code == 429
    assert response.json()["code"] == "quota_exceeded"
    assert int(response.headers["retry-after"]) > 0


def test_verified_faculty_uses_the_verified_limit(client, auth_headers, monkeypatch):
    monkeypatch.setenv("GENERATE_LIMIT_VERIFIED", "4")
    headers = auth_headers(role="faculty", verified=True)
    statuses = [client.post(PLAIN, json=BODY, headers=headers).status_code for _ in range(5)]
    assert statuses == [200, 200, 200, 200, 429]


def test_admin_is_unlimited(client, auth_headers, store):
    headers = auth_headers(role="admin")
    assert all(client.post(PLAIN, json=BODY, headers=headers).status_code == 200 for _ in range(6))
    assert store.usage == {}


def test_stream_emits_meta_question_done_and_counts_one(client, auth_headers, store):
    response = client.post(STREAM, json=BODY, headers=auth_headers(role="faculty"))
    assert [e["type"] for e in events(response)] == ["meta", "question", "done"]
    assert sum(store.usage.values()) == 1


def test_stream_refunds_when_every_question_failed(client, auth_headers, store, fake_rag):
    fake_rag.question_error = True
    client.post(STREAM, json=BODY, headers=auth_headers(role="faculty"))
    assert sum(store.usage.values()) == 0


def test_stream_refunds_when_no_source_papers(client, auth_headers, store, fake_rag):
    fake_rag.papers = []
    response = client.post(STREAM, json=BODY, headers=auth_headers(role="faculty"))
    assert events(response) == [{"type": "error", "message": "No past papers found for this subject yet."}]
    assert sum(store.usage.values()) == 0


def test_stream_error_is_generic_and_refunded(client, auth_headers, store, fake_rag):
    fake_rag.raise_on_generate = True
    response = client.post(STREAM, json=BODY, headers=auth_headers(role="faculty"))
    assert events(response)[-1] == {"type": "error", "message": "Paper generation failed. Please try again."}
    assert "secret" not in response.text
    assert sum(store.usage.values()) == 0


def test_plain_generation_without_papers_is_404_and_refunded(client, auth_headers, store, fake_rag):
    fake_rag.papers = []
    response = client.post(PLAIN, json=BODY, headers=auth_headers(role="faculty"))
    assert response.status_code == 404 and response.json()["code"] == "not_found"
    assert sum(store.usage.values()) == 0


def test_plain_generation_refunds_when_every_question_failed(client, auth_headers, store, fake_rag):
    fake_rag.question_error = True
    response = client.post(PLAIN, json=BODY, headers=auth_headers(role="faculty"))
    assert response.status_code == 200
    assert sum(store.usage.values()) == 0


def test_missing_fields_are_400_without_using_quota(client, auth_headers, store):
    response = client.post(STREAM, json={"subject": "x"}, headers=auth_headers(role="faculty"))
    assert response.status_code == 400 and response.json()["code"] == "invalid_request"
    assert sum(store.usage.values()) == 0
