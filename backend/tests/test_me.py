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
