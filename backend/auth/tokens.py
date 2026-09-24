import os
from dataclasses import dataclass

import jwt
from jwt import PyJWKClient

JWKS_URL = "https://login.microsoftonline.com/common/discovery/v2.0/keys"
REQUIRED_SCOPE = "access_as_user"
LEEWAY_SECONDS = 60


class TokenError(Exception):
    """The access token is missing required properties or failed validation."""


@dataclass(frozen=True)
class Claims:
    tid: str
    oid: str
    name: str | None  # display only, never used for authorization
    email: str | None  # display only, never used for authorization


_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        # Keys are cached; an unknown key id triggers a refetch.
        _jwks_client = PyJWKClient(JWKS_URL, cache_keys=True, lifespan=3600)
    return _jwks_client


def verify_access_token(token: str) -> Claims:
    """Validates a Microsoft identity platform v2 access token issued for this API."""
    client_id = os.getenv("AZURE_CLIENT_ID")
    if not client_id:
        raise TokenError("AZURE_CLIENT_ID is not configured")
    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
        # Multi-tenant app: the expected issuer depends on the tenant the token names.
        tid = jwt.decode(token, options={"verify_signature": False}).get("tid")
        if not tid:
            raise TokenError("token has no tenant id")
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=client_id,
            issuer=f"https://login.microsoftonline.com/{tid}/v2.0",
            leeway=LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "aud", "iss"]},
        )
    except TokenError:
        raise
    except jwt.PyJWTError as error:
        raise TokenError(str(error)) from error

    if REQUIRED_SCOPE not in (payload.get("scp") or "").split():
        raise TokenError(f"token lacks the {REQUIRED_SCOPE} scope")
    oid = payload.get("oid")
    if not oid:
        raise TokenError("token has no object id")
    return Claims(
        tid=tid,
        oid=oid,
        name=payload.get("name"),
        email=payload.get("preferred_username") or payload.get("email"),
    )
