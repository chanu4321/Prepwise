from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from auth.deps import current_user, get_user_store, user_subject
from auth.limits import limits_for
from auth.users import PostgresUserStore, User
from errors import ApiError

router = APIRouter()


class RoleChoice(BaseModel):
    role: Literal["student", "faculty"]


def me_payload(user: User, store: PostgresUserStore) -> dict:
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "role": user.role,
        "verified": user.verified,
        "limits": limits_for(user),
        "usedToday": store.usage_today(user_subject(user)),
    }


@router.get("/me")
def get_me(user: User = Depends(current_user), store: PostgresUserStore = Depends(get_user_store)):
    store.touch(user.id)
    return me_payload(user, store)


@router.post("/me/role")
def choose_role(choice: RoleChoice, user: User = Depends(current_user),
                store: PostgresUserStore = Depends(get_user_store)):
    if not store.set_role_once(user.id, choice.role):
        raise ApiError(409, "role_already_set", "Your role is already set. Ask an admin if it needs to change.")
    return me_payload(store.get(user.id), store)
