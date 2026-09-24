from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from auth.deps import get_user_store, require_role, user_subject
from auth.users import PostgresUserStore, User, user_summary
from errors import ApiError

router = APIRouter(prefix="/admin")


class UserUpdate(BaseModel):
    role: Literal["student", "faculty", "admin"] | None = None
    verified: bool | None = None


@router.get("/users")
def list_users(admin: User = Depends(require_role("admin")), store: PostgresUserStore = Depends(get_user_store)):
    return {"users": store.list_with_usage()}


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserUpdate, admin: User = Depends(require_role("admin")),
                store: PostgresUserStore = Depends(get_user_store)):
    if body.role is None and body.verified is None:
        raise ApiError(400, "invalid_request", "Nothing to update.")
    if user_id == admin.id and body.role is not None and body.role != "admin":
        raise ApiError(409, "cannot_demote_self", "You can't remove your own admin role.")
    updated = store.update(user_id, role=body.role, verified=body.verified)
    if updated is None:
        raise ApiError(404, "not_found", "User not found.")
    return user_summary(updated, store.usage_today(user_subject(updated)))
