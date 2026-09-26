def test_me_requires_sign_in(client, store):
    response = client.get("/api/v1/me")
    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"


def test_first_call_creates_user_without_role(client, store, make_token):
    response = client.get("/api/v1/me", headers={"Authorization": f"Bearer {make_token(oid='brand-new')}"})
    assert response.status_code == 200
    body = response.json()
    assert body["role"] is None
    assert body["verified"] is False
    assert body["limits"] == {"generate": 0, "upload": 0, "syllabus": 0}
    assert body["usedToday"] == {"generate": 0, "upload": 0, "syllabus": 0}
    assert store.touched == [body["id"]]


def test_trial_faculty_limits(client, auth_headers):
    body = client.get("/api/v1/me", headers=auth_headers(role="faculty")).json()
    assert body["limits"] == {"generate": 3, "upload": 0, "syllabus": 0}


def test_admin_limits_are_unlimited(client, auth_headers):
    body = client.get("/api/v1/me", headers=auth_headers(role="admin")).json()
    assert body["limits"] == {"generate": None, "upload": None, "syllabus": None}


def test_role_can_be_chosen_once(client, auth_headers):
    headers = auth_headers()
    first = client.post("/api/v1/me/role", json={"role": "student"}, headers=headers)
    assert first.status_code == 200
    assert first.json()["role"] == "student"
    assert first.json()["limits"]["upload"] == 20

    second = client.post("/api/v1/me/role", json={"role": "faculty"}, headers=headers)
    assert second.status_code == 409
    assert second.json()["code"] == "role_already_set"


def test_admin_cannot_be_self_assigned(client, auth_headers):
    response = client.post("/api/v1/me/role", json={"role": "admin"}, headers=auth_headers())
    assert response.status_code == 422


def test_me_uploads_lists_only_my_uploads_newest_first(client, auth_headers, store):
    from services.paper_store import get_paper_store
    from tests.fakes import FakePaperStore

    papers = FakePaperStore()
    client.app.dependency_overrides[get_paper_store] = lambda: papers
    headers = auth_headers(role="student")
    me_id = max(store.users)
    papers.add_paper(filename="old.pdf", uploaded_by=me_id)
    papers.add_paper(filename="theirs.pdf", uploaded_by=me_id + 1)
    papers.add_paper(filename="new.pdf", status="review", uploaded_by=me_id)

    response = client.get("/api/v1/me/uploads", headers=headers)
    assert response.status_code == 200
    assert [u["filename"] for u in response.json()["uploads"]] == ["new.pdf", "old.pdf"]
    assert client.get("/api/v1/me/uploads").status_code == 401
