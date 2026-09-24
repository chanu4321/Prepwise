from tests.fakes import fake_db_cursor


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
