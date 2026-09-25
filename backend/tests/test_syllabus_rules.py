import pytest

URL = "/api/v1/syllabus/upload"


@pytest.fixture
def syllabus(monkeypatch):
    state = {"owner": None, "exists": False, "saved": [], "success": True}

    def fake_owner(code):
        return (state["exists"], state["owner"])

    def fake_save(file_bytes, filename, subject_code, subject_name, uploaded_by=None):
        state["saved"].append({"code": subject_code, "uploaded_by": uploaded_by})
        if not state["success"]:
            return {"success": False, "error": "Database error: password=x"}
        return {"success": True, "data": {"modules": []}}

    monkeypatch.setattr("api.routes.get_syllabus_owner", fake_owner)
    monkeypatch.setattr("api.routes.process_and_save_syllabus", fake_save)
    return state


def post(client, headers):
    return client.post(URL, files={"file": ("syl.pdf", b"%PDF", "application/pdf")},
                       data={"subject_code": "CSE432", "subject_name": "SPM"}, headers=headers)


def verified_id(store):
    return max(store.users)  # id of the most recently created user


def test_trial_faculty_and_students_cannot_upload_syllabi(client, auth_headers, syllabus):
    assert post(client, auth_headers(role="faculty")).status_code == 403
    assert post(client, auth_headers(role="student")).status_code == 403
    assert syllabus["saved"] == []


def test_verified_faculty_creates_new_syllabus_as_owner(client, auth_headers, store, syllabus):
    headers = auth_headers(role="faculty", verified=True)
    assert post(client, headers).status_code == 200
    assert syllabus["saved"][0]["uploaded_by"] == verified_id(store)


def test_only_owner_or_admin_can_replace(client, auth_headers, store, syllabus):
    owner_headers = auth_headers(role="faculty", verified=True)
    syllabus.update(exists=True, owner=verified_id(store))
    other_headers = auth_headers(role="faculty", verified=True)
    assert post(client, other_headers).status_code == 403
    assert post(client, owner_headers).status_code == 200
    assert post(client, auth_headers(role="admin")).status_code == 200


def test_legacy_syllabus_is_admin_only(client, auth_headers, syllabus):
    syllabus.update(exists=True, owner=None)
    assert post(client, auth_headers(role="faculty", verified=True)).status_code == 403
    assert post(client, auth_headers(role="admin")).status_code == 200


def test_processing_failure_is_422_generic_and_refunded(client, auth_headers, store, syllabus):
    syllabus["success"] = False
    response = post(client, auth_headers(role="faculty", verified=True))
    assert response.status_code == 422 and response.json()["code"] == "syllabus_unreadable"
    assert "password" not in response.text
    assert sum(store.usage.values()) == 0
