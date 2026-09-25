import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from auth import tokens
from auth.tokens import Claims, TokenError, verify_access_token, _get_jwks_client as real_get_jwks_client
from tests.conftest import TEST_TID

PERSONAL_TID = "9188040d-6c67-4c5b-b112-36a304b66dad"


def test_valid_token_returns_claims(make_token):
    claims = verify_access_token(make_token())
    assert claims == Claims(tid=TEST_TID, oid="oid-1", name="Test User", email="test@example.com")


def test_personal_account_token_is_accepted(make_token):
    token = make_token(tid=PERSONAL_TID, iss=f"https://login.microsoftonline.com/{PERSONAL_TID}/v2.0")
    assert verify_access_token(token).tid == PERSONAL_TID


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "someone-elses-app"},
        {"iss": "https://login.microsoftonline.com/22222222-2222-2222-2222-222222222222/v2.0"},
        {"iss": "https://sts.windows.net/11111111-1111-1111-1111-111111111111/"},
        {"exp": int(time.time()) - 120},
        {"scp": "User.Read"},
        {"scp": None},
        {"oid": None},
        {"tid": None},
    ],
    ids=["wrong-audience", "issuer-other-tenant", "v1-issuer", "expired", "wrong-scope", "no-scope", "no-oid", "no-tid"],
)
def test_invalid_claims_are_rejected(make_token, overrides):
    with pytest.raises(TokenError):
        verify_access_token(make_token(**overrides))


def test_token_signed_with_another_key_is_rejected():
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    forged = jwt.encode(
        {"aud": "test-client-id", "iss": f"https://login.microsoftonline.com/{TEST_TID}/v2.0", "tid": TEST_TID,
         "oid": "oid-1", "sub": "s", "scp": "access_as_user", "iat": now, "nbf": now, "exp": now + 3600},
        other_key, algorithm="RS256",
    )
    with pytest.raises(TokenError):
        verify_access_token(forged)


def test_malformed_token_is_rejected():
    with pytest.raises(TokenError):
        verify_access_token("not-a-jwt")


def test_missing_client_id_configuration_is_rejected(make_token, monkeypatch):
    monkeypatch.delenv("AZURE_CLIENT_ID")
    with pytest.raises(TokenError):
        verify_access_token(make_token())


def test_jwks_client_does_not_lru_cache_keys(monkeypatch):
    """Verify that get_signing_key is not lru-cached forever.

    PyJWKClient with cache_keys=True would wrap get_signing_key with lru_cache,
    causing withdrawn keys (e.g., after compromise) to be accepted until restart.
    The JWK set itself is cached for lifespan=3600, which is correct;
    unknown key IDs trigger a refetch of the set.
    """
    # Reset the global client so we build a fresh PyJWKClient
    monkeypatch.setattr(tokens, "_jwks_client", None)

    # Build a real PyJWKClient (no network I/O)
    client = real_get_jwks_client()

    # Verify the JWK set is cached (by design)
    assert client.jwk_set_cache is not None, "JWK set should be cached"

    # Verify get_signing_key is NOT lru-cached (no cache_info attribute)
    assert not hasattr(client.get_signing_key, "cache_info"), \
        "get_signing_key should not be lru-cached; withdrawn keys would be accepted until restart"
