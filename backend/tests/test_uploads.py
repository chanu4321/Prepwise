import pytest
from fastapi.testclient import TestClient

from tests.fakes import FakeProcessor, FakeVectorService

URL = "/api/v1/documents/ingest"
PDF = ("paper.pdf", b"%PDF-1.4 fake", "application/pdf")


@pytest.fixture(autouse=True)
def pipeline(monkeypatch, tmp_path):
    inserted = []

    def fake_insert(filename, file_path, fields, uploaded_by):
        inserted.append({"filename": filename, "fields": fields, "uploaded_by": uploaded_by})
        return 100 + len(inserted)

    monkeypatch.setattr("api.routes.processor", FakeProcessor(tmp_path))
    monkeypatch.setattr("api.routes.insert_paper", fake_insert)
    monkeypatch.setattr("api.routes.VectorService", FakeVectorService)
    return inserted


def upload(client, headers=None, file=PDF):
    return client.post(URL, files={"file": file}, headers=headers or {})


def test_anonymous_upload_is_saved_with_normalized_code(client, store, pipeline):
    response = upload(client)
    assert response.status_code == 200
    assert pipeline[0]["uploaded_by"] is None
    assert pipeline[0]["fields"]["subject_code"] == "AIML201"


def test_anonymous_limit_is_per_ip(client, store):
    assert [upload(client).status_code for _ in range(6)] == [200] * 5 + [429]
    other_ip = TestClient(client.app, client=("198.51.100.7", 50000))
    assert upload(other_ip).status_code == 200


def test_anonymous_usage_is_stored_as_hash(client, store):
    upload(client)
    (subject, action), = store.usage.keys()
    assert subject.startswith("ip:") and "testclient" not in subject and action == "upload"


def test_student_limit(client, auth_headers, monkeypatch):
    monkeypatch.setenv("UPLOAD_LIMIT_STUDENT", "2")
    headers = auth_headers(role="student")
    assert [upload(client, headers).status_code for _ in range(3)] == [200, 200, 429]


def test_trial_faculty_cannot_upload_papers(client, auth_headers, store):
    response = upload(client, auth_headers(role="faculty"))
    assert response.status_code == 403 and response.json()["code"] == "forbidden"
    assert store.usage == {}


def test_verified_faculty_and_admin_can_upload(client, auth_headers, store, pipeline):
    assert upload(client, auth_headers(role="faculty", verified=True)).status_code == 200
    assert upload(client, auth_headers(role="admin")).status_code == 200
    assert pipeline[0]["uploaded_by"] is not None


def test_user_without_role_must_choose_first(client, auth_headers):
    assert upload(client, auth_headers()).json()["code"] == "role_required"


def test_non_pdf_is_rejected_without_using_quota(client, store):
    response = upload(client, file=("notes.txt", b"hello", "text/plain"))
    assert response.status_code == 400 and response.json()["code"] == "invalid_file"
    assert store.usage == {}


def test_pipeline_failure_is_generic_and_refunded(client, store, monkeypatch, tmp_path):
    monkeypatch.setattr("api.routes.processor", FakeProcessor(tmp_path, fail=True))
    response = upload(client)
    assert response.status_code == 500 and "secret" not in response.text
    assert sum(store.usage.values()) == 0
