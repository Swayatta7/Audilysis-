from dataclasses import dataclass


@dataclass(frozen=True)
class OwnerContext:
    user_id: int | None
    owner_key: str
    owner_type: str
    secure_auth: bool


def extract_authenticated_user_id(session_like) -> int | None:
    if not session_like:
        return None
    for key in (
        "auth_user_id",
        "user_id",
        "current_user_id",
        "authenticated_user_id",
        "account_user_id",
    ):
        value = session_like.get(key)
        if value in (None, ""):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def build_owner_context(user_id: int | None, browser_session_id: str, allow_dev_session_fallback: bool) -> OwnerContext:
    if user_id is not None:
        return OwnerContext(
            user_id=user_id,
            owner_key=f"user:{user_id}",
            owner_type="user",
            secure_auth=True,
        )
    if allow_dev_session_fallback:
        return OwnerContext(
            user_id=None,
            owner_key=f"session:{browser_session_id}",
            owner_type="session",
            secure_auth=False,
        )
    return OwnerContext(
        user_id=None,
        owner_key="",
        owner_type="anonymous",
        secure_auth=False,
    )
