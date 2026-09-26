from types import SimpleNamespace

import pytest

from services.paper_store import get_paper_store
from tests.fakes import FakePaperStore, fake_db_cursor


def test_syllabus_read_is_faculty_only(client, auth_headers, monkeypatch):
    monkeypatch.setattr("api.routes.get_syllabus_by_code", lambda code: {"subject_code": code, "modules": []})
    assert client.get("/api/v1/syllabus/CSE432").status_code == 401
    assert client.get("/api/v1/syllabus/CSE432", headers=auth_headers(role="student")).status_code == 403
    trial = client.get("/api/v1/syllabus/CSE432", headers=auth_headers(role="faculty"))
    assert trial.status_code == 200 and trial.json()["data"]["subject_code"] == "CSE432"


def test_missing_syllabus_is_404_with_code(client, auth_headers, monkeypatch):
    monkeypatch.setattr("api.routes.get_syllabus_by_code", lambda code: None)
    response = client.get("/api/v1/syllabus/NOPE", headers=auth_headers(role="faculty"))
    assert response.status_code == 404 and response.json()["code"] == "not_found"


def test_download_of_unknown_paper_is_404_not_500(client, store, monkeypatch):
    monkeypatch.setattr("api.routes.db_cursor", lambda: fake_db_cursor([]))
    response = client.get("/api/v1/documents/999/download")
    assert response.status_code == 404 and response.json()["code"] == "not_found"


def test_download_finds_the_file_whatever_the_current_directory(client, store, monkeypatch, tmp_path):
    root = tmp_path / "project"
    (root / "backend" / "papers").mkdir(parents=True)
    (root / "backend" / "papers" / "SPM 23.pdf").write_bytes(b"%PDF-1.4 spm")
    monkeypatch.setattr("services.paper_store.PROJECT_ROOT", root)
    monkeypatch.setattr("api.routes.db_cursor", lambda: fake_db_cursor([("backend/papers/SPM 23.pdf", "SPM 23.pdf")]))
    monkeypatch.chdir(tmp_path)

    response = client.get("/api/v1/documents/1/download")
    assert response.status_code == 200
    assert response.content == b"%PDF-1.4 spm"


def test_documents_list_is_public(client, store, monkeypatch):
    row = (1, "SPM 23.pdf", "CSE432", "SPM", None, "June, 2023", "3 Hrs.", "60")
    monkeypatch.setattr("api.routes.db_cursor", lambda: fake_db_cursor([row]))
    response = client.get("/api/v1/documents")
    assert response.status_code == 200
    assert response.json()[0]["subjectCode"] == "CSE432"


def test_database_failure_is_a_generic_500(client, store, monkeypatch):
    def broken():
        raise RuntimeError("could not connect to server at 10.0.0.5 password=x")
    monkeypatch.setattr("api.routes.db_cursor", broken)
    response = client.get("/api/v1/documents")
    assert response.status_code == 500 and response.json()["code"] == "internal_error"
    assert "10.0.0.5" not in response.text


def test_public_list_and_download_only_show_live_papers(client, store, monkeypatch):
    executed = []
    monkeypatch.setattr("api.routes.db_cursor", lambda: fake_db_cursor([], executed))
    client.get("/api/v1/documents")
    client.get("/api/v1/documents/5/download")
    assert len(executed) == 2
    assert all("status = 'live'" in sql for sql, _ in executed)


def test_semantic_search_only_joins_live_papers(client, store, monkeypatch):
    executed = []

    class Vectors:
        def search_similar(self, query, limit):
            return [SimpleNamespace(id=4, score=0.8)]

    monkeypatch.setattr("api.routes.VectorService", Vectors)
    monkeypatch.setattr("api.routes.db_cursor", lambda: fake_db_cursor([], executed))
    client.post("/api/v1/search/semantic", json={"query": "risk"})
    (sql, params), = executed
    assert "status = 'live'" in sql and params == (4,)


@pytest.fixture
def papers(client):
    fake = FakePaperStore()
    client.app.dependency_overrides[get_paper_store] = lambda: fake
    return fake


def test_uploader_can_check_status_with_the_upload_key(client, store, papers):
    paper = papers.add_paper(filename="SPM 23.pdf", status="rejected", note="Not an exam paper.")
    response = client.get(f"/api/v1/documents/uploads/{paper.upload_key}")
    assert response.status_code == 200
    assert response.json() == {"id": paper.id, "filename": "SPM 23.pdf", "status": "rejected",
                               "note": "Not an exam paper.", "uploadedAt": "2026-09-26T12:00:00"}


# (A key with "/" or ".." never reaches this route: the URL doesn't match it, so it's a plain 404 either way.)
@pytest.mark.parametrize("key", ["0" * 32, "abc", "A" * 32, "a" * 200, "g" * 32])
def test_unknown_or_malformed_upload_key_is_404(client, store, papers, key, monkeypatch):
    looked_up = []
    monkeypatch.setattr(papers, "get_by_upload_key", lambda k: looked_up.append(k))
    response = client.get(f"/api/v1/documents/uploads/{key}")
    assert response.status_code == 404 and response.json()["code"] == "not_found"
    assert looked_up == ([key] if key == "0" * 32 else [])
