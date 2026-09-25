import os
from datetime import datetime, timedelta, timezone

from auth.users import User


def _limit(name: str, default: int) -> int:
    return int(os.getenv(name) or default)


# None = unlimited, 0 = not allowed. Values are read on every call so env changes apply.
def generate_limit(user: User) -> int | None:
    if user.role == "admin":
        return None
    if user.role == "faculty":
        return _limit("GENERATE_LIMIT_VERIFIED", 25) if user.verified else _limit("GENERATE_LIMIT_TRIAL", 3)
    return 0


def upload_limit(user: User | None) -> int | None:
    if user is None:
        return _limit("UPLOAD_LIMIT_ANON", 5)
    if user.role == "admin":
        return None
    if user.role == "student":
        return _limit("UPLOAD_LIMIT_STUDENT", 20)
    if user.role == "faculty" and user.verified:
        return _limit("UPLOAD_LIMIT_FACULTY", 10)
    return 0


def syllabus_limit(user: User) -> int | None:
    if user.role == "admin":
        return None
    if user.role == "faculty" and user.verified:
        return _limit("SYLLABUS_LIMIT", 10)
    return 0


def limits_for(user: User) -> dict[str, int | None]:
    return {"generate": generate_limit(user), "upload": upload_limit(user), "syllabus": syllabus_limit(user)}


def seconds_until_utc_midnight(now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((next_midnight - now).total_seconds()))
