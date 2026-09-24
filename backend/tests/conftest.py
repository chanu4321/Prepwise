import os

# Tests must never reach real services. python-dotenv does not override variables that are
# already set, so fixing them here (before any backend module loads .env) keeps tests offline.
os.environ["DATABASE_URL"] = "postgresql://tests-must-not-use-the-real-db@127.0.0.1:1/none"
os.environ["QDRANT_URL"] = "http://127.0.0.1:1"
os.environ["QDRANT_API_KEY"] = ""
os.environ["NVIDIA_API_KEY"] = "test-key"
os.environ["NVIDIA_EMBED_API_KEY"] = "test-key"
os.environ["AZURE_CLIENT_ID"] = "test-client-id"
os.environ["IP_HASH_SALT"] = "test-salt"
os.environ["GENERATE_LIMIT_TRIAL"] = "3"
os.environ["GENERATE_LIMIT_VERIFIED"] = "25"
os.environ["UPLOAD_LIMIT_ANON"] = "5"
os.environ["UPLOAD_LIMIT_STUDENT"] = "20"
os.environ["UPLOAD_LIMIT_FACULTY"] = "10"
os.environ["SYLLABUS_LIMIT"] = "10"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def client():
    from main import app

    # Not used as a context manager, so the lifespan (init_db against Postgres) never runs.
    yield TestClient(app)
    app.dependency_overrides.clear()


import time  # noqa: E402

import jwt  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

TEST_TID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(scope="session")
def rsa_keys():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


class _FakeSigningKey:
    def __init__(self, key):
        self.key = key


class _FakeJwksClient:
    def __init__(self, public_key):
        self.public_key = public_key

    def get_signing_key_from_jwt(self, token):
        return _FakeSigningKey(self.public_key)


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch, rsa_keys):
    """Every test validates tokens against the test key pair instead of Microsoft's keys."""
    from auth import tokens

    monkeypatch.setattr(tokens, "_get_jwks_client", lambda: _FakeJwksClient(rsa_keys[1]))


@pytest.fixture
def make_token(rsa_keys):
    def _make(**overrides):
        now = int(time.time())
        claims = {
            "aud": "test-client-id",
            "iss": f"https://login.microsoftonline.com/{TEST_TID}/v2.0",
            "tid": TEST_TID,
            "oid": "oid-1",
            "sub": "sub-1",
            "scp": "access_as_user",
            "name": "Test User",
            "preferred_username": "test@example.com",
            "ver": "2.0",
            "iat": now,
            "nbf": now,
            "exp": now + 3600,
        }
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, rsa_keys[0], algorithm="RS256", headers={"kid": "test-key"})

    return _make
