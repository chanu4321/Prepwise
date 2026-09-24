def test_only_admins_can_list_users(client, auth_headers, store):
    assert client.get("/api/v1/admin/users").status_code == 401
    assert client.get("/api/v1/admin/users", headers=auth_headers(role="faculty", verified=True)).status_code == 403
    response = client.get("/api/v1/admin/users", headers=auth_headers(role="admin"))
    assert response.status_code == 200
    assert {"id", "email", "name", "role", "verified", "createdAt", "usedToday"} <= set(response.json()["users"][0])


def test_admin_can_verify_and_change_role(client, auth_headers, store):
    auth_headers(role="faculty")
    target_id = max(store.users)
    admin = auth_headers(role="admin")
    response = client.patch(f"/api/v1/admin/users/{target_id}", json={"verified": True}, headers=admin)
    assert response.status_code == 200 and response.json()["verified"] is True
    response = client.patch(f"/api/v1/admin/users/{target_id}", json={"role": "student"}, headers=admin)
    assert response.json()["role"] == "student"


def test_admin_cannot_demote_self(client, auth_headers, store):
    admin = auth_headers(role="admin")
    admin_id = max(store.users)
    response = client.patch(f"/api/v1/admin/users/{admin_id}", json={"role": "student"}, headers=admin)
    assert response.status_code == 409 and response.json()["code"] == "cannot_demote_self"


def test_unknown_user_and_empty_update(client, auth_headers):
    admin = auth_headers(role="admin")
    assert client.patch("/api/v1/admin/users/9999", json={"verified": True}, headers=admin).status_code == 404
    assert client.patch("/api/v1/admin/users/1", json={}, headers=admin).status_code == 400
    assert client.patch("/api/v1/admin/users/1", json={"role": "superuser"}, headers=admin).status_code == 422
