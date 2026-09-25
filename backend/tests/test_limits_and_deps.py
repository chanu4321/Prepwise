import hashlib
from datetime import datetime, timezone

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from auth import limits
from auth.deps import (current_user, enforce_quota, get_user_store, ip_subject, optional_user,
                       quota_subject, require_role)
from auth.users import User
from errors import ApiError, install_error_handling
from tests.fakes import FakeUserStore


def user(role, verified=False):
    return User(id=7, ms_tid="t", ms_oid="o", email=None, name=None, role=role, verified=verified)


@pytest.mark.parametrize("role,verified,expected", [
    ("admin", False, None), ("faculty", True, 25), ("faculty", False, 3), ("student", False, 0), (None, False, 0),
])
def test_generate_limit(role, verified, expected):
    assert limits.generate_limit(user(role, verified)) == expected


@pytest.mark.parametrize("u,expected", [
    (None, 5), (user("admin"), None), (user("student"), 20), (user("faculty", True), 10),
    (user("faculty", False), 0), (user(None), 0),
])
def test_upload_limit(u, expected):
    assert limits.upload_limit(u) == expected


@pytest.mark.parametrize("role,verified,expected", [
    ("admin", False, None), ("faculty", True, 10), ("faculty", False, 0), ("student", False, 0),
])
def test_syllabus_limit(role, verified, expected):
    assert limits.syllabus_limit(user(role, verified)) == expected


def test_limits_follow_environment(monkeypatch):
    monkeypatch.setenv("GENERATE_LIMIT_TRIAL", "7")
    assert limits.generate_limit(user("faculty")) == 7


def test_seconds_until_utc_midnight():
    assert limits.seconds_until_utc_midnight(datetime(2026, 9, 25, 23, 59, 0, tzinfo=timezone.utc)) == 60
    assert limits.seconds_until_utc_midnight(datetime(2026, 9, 25, 0, 0, 0, tzinfo=timezone.utc)) == 86400


@pytest.fixture
def probe(make_token):
    """A tiny app exposing each dependency, sharing one fake store."""
    app = FastAPI()
    install_error_handling(app)
    fake = FakeUserStore()
    app.dependency_overrides[get_user_store] = lambda: fake

    @app.get("/optional")
    def optional(u=Depends(optional_user)):
        return {"id": u.id if u else None}

    @app.get("/current")
    def current(u=Depends(current_user)):
        return {"id": u.id}

    @app.get("/faculty")
    def faculty(u=Depends(require_role("faculty", "admin"))):
        return {"role": u.role}

    @app.get("/subject")
    def subject(request: Request, u=Depends(optional_user)):
        return {"subject": quota_subject(u, request)}

    return TestClient(app), fake, make_token


def test_no_token_is_anonymous_for_optional_and_401_for_current(probe):
    client, _, _ = probe
    assert client.get("/optional").json() == {"id": None}
    response = client.get("/current")
    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"
    assert response.headers["www-authenticate"] == "Bearer"


def test_bad_token_is_401_even_where_login_is_optional(probe):
    client, _, make_token = probe
    response = client.get("/optional", headers={"Authorization": f"Bearer {make_token(aud='other')}"})
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


def test_first_request_creates_the_user(probe):
    client, fake, make_token = probe
    response = client.get("/current", headers={"Authorization": f"Bearer {make_token(oid='new-person')}"})
    assert response.status_code == 200
    assert len(fake.users) == 1


def test_require_role_distinguishes_no_role_from_wrong_role(probe):
    client, fake, make_token = probe
    headers = {"Authorization": f"Bearer {make_token(oid='p1')}"}
    assert client.get("/faculty", headers=headers).json()["code"] == "role_required"
    fake.update(1, role="student")
    response = client.get("/faculty", headers=headers)
    assert response.status_code == 403 and response.json()["code"] == "forbidden"
    fake.update(1, role="faculty")
    assert client.get("/faculty", headers=headers).status_code == 200


def test_anonymous_subject_is_a_salted_hash_not_the_ip(probe):
    client, _, _ = probe
    subject = client.get("/subject").json()["subject"]
    # IP_HASH_SALT is "test-salt" (conftest); TestClient's default client host is "testclient",
    # which isn't a parseable IP, so it's hashed as-is rather than normalised by ip_subject.
    expected = "ip:" + hashlib.sha256(("test-salt" + "testclient").encode()).hexdigest()
    assert subject == expected


def request_from(host):
    return Request({"type": "http", "client": (host, 1), "headers": []})


def test_ip_subject_differs_per_address():
    assert ip_subject(request_from("203.0.113.1")) != ip_subject(request_from("203.0.113.2"))


def test_ipv6_addresses_in_the_same_64_share_a_subject():
    """An IPv6 client controls a whole /64, so two addresses in it must map to one bucket."""
    assert ip_subject(request_from("2001:db8:1:2::1")) == ip_subject(request_from("2001:db8:1:2:ffff::9"))


def test_ipv6_addresses_in_a_different_64_have_different_subjects():
    assert ip_subject(request_from("2001:db8:1:2::1")) != ip_subject(request_from("2001:db8:1:3::1"))


def test_ipv4_mapped_ipv6_matches_the_plain_ipv4_address():
    assert ip_subject(request_from("::ffff:203.0.113.1")) == ip_subject(request_from("203.0.113.1"))


def test_enforce_quota_raises_429_with_retry_after():
    fake = FakeUserStore()
    enforce_quota(fake, "user:1", "generate", 1)
    with pytest.raises(ApiError) as caught:
        enforce_quota(fake, "user:1", "generate", 1)
    assert caught.value.status_code == 429
    assert caught.value.code == "quota_exceeded"
    assert int(caught.value.headers["Retry-After"]) > 0


def test_enforce_quota_skips_unlimited_and_refuses_zero():
    fake = FakeUserStore()
    for _ in range(50):
        enforce_quota(fake, "user:1", "generate", None)
    assert fake.usage == {}
    with pytest.raises(ApiError) as caught:
        enforce_quota(fake, "user:1", "generate", 0)
    assert caught.value.status_code == 403


@pytest.mark.parametrize("header_value", [
    "Bearer",
    "Bearer ",
    "Basic abc",
    "garbage",
])
def test_malformed_authorization_header_is_401_not_anonymous(probe, header_value):
    """Malformed Authorization headers should be 401, not silently treated as anonymous."""
    client, _, _ = probe
    response = client.get("/optional", headers={"Authorization": header_value})
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"
    assert response.headers["www-authenticate"] == "Bearer"
