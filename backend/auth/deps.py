import hashlib
import ipaddress
import logging
import os

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from auth.limits import seconds_until_utc_midnight
from auth.tokens import TokenError, verify_access_token
from auth.users import PostgresUserStore, User
from errors import ApiError

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)
_BEARER_CHALLENGE = {"WWW-Authenticate": "Bearer"}


def get_user_store() -> PostgresUserStore:
    return PostgresUserStore()


def optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    store: PostgresUserStore = Depends(get_user_store),
    request: Request = None,
) -> User | None:
    """The signed-in user, or None without a token. A token that fails validation is always a 401."""
    if credentials is None:
        # Check if an Authorization header was present but malformed
        auth_header = request.headers.get("authorization") if request else None
        if auth_header:
            logger.info("Rejected malformed Authorization header")
            raise ApiError(401, "invalid_token", "Your sign-in has expired or is invalid. Please sign in again.",
                           headers=_BEARER_CHALLENGE)
        return None
    try:
        claims = verify_access_token(credentials.credentials)
    except TokenError as error:
        logger.info("Rejected access token: %s", error)
        raise ApiError(401, "invalid_token", "Your sign-in has expired or is invalid. Please sign in again.",
                       headers=_BEARER_CHALLENGE)
    return store.get_or_create(claims)


def current_user(user: User | None = Depends(optional_user)) -> User:
    if user is None:
        raise ApiError(401, "not_authenticated", "Please sign in to continue.", headers=_BEARER_CHALLENGE)
    return user


def require_role(*roles: str):
    def dependency(user: User = Depends(current_user)) -> User:
        if user.role is None:
            raise ApiError(403, "role_required", "Choose Student or Faculty to continue.")
        if user.role not in roles:
            raise ApiError(403, "forbidden", "Your account can't do this.")
        return user

    return dependency


def user_subject(user: User) -> str:
    return f"user:{user.id}"


def _normalized_ip(host: str) -> str:
    """Collapses an address to its anonymous-quota bucket key.

    An IPv6 client controls a whole /64, so it's normalised to its /64 network to stop an
    attacker rotating addresses within that block for fresh buckets. An IPv4-mapped IPv6 address
    (::ffff:a.b.c.d) is normalised to the plain IPv4 form it represents. IPv4 addresses, and
    anything that doesn't parse as an IP (e.g. "unknown", TestClient's "testclient"), are kept as-is.
    """
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return host
    if isinstance(addr, ipaddress.IPv6Address):
        mapped = addr.ipv4_mapped
        if mapped is not None:
            return str(mapped)
        return str(ipaddress.ip_network(f"{addr}/64", strict=False).network_address) + "/64"
    return host


def ip_subject(request: Request) -> str:
    salt = os.getenv("IP_HASH_SALT")
    if not salt:
        raise RuntimeError("IP_HASH_SALT is not configured")
    ip = request.client.host if request.client else "unknown"
    ip = _normalized_ip(ip)
    return "ip:" + hashlib.sha256((salt + ip).encode()).hexdigest()


def quota_subject(user: User | None, request: Request) -> str:
    return user_subject(user) if user is not None else ip_subject(request)


def enforce_quota(store, subject: str, action: str, limit: int | None) -> None:
    """Counts one use of `action`. None = unlimited (not counted); callers reject 0 with a specific message."""
    if limit is None:
        return
    if limit <= 0:
        raise ApiError(403, "forbidden", "Your account can't do this.")
    if not store.consume_quota(subject, action, limit):
        raise ApiError(
            429, "quota_exceeded", f"You've reached today's limit of {limit}. It resets at midnight UTC.",
            headers={"Retry-After": str(seconds_until_utc_midnight())},
        )


def refund_quota_if_counted(store, subject: str, action: str, limit: int | None) -> None:
    if limit is not None:
        store.refund_quota(subject, action)
