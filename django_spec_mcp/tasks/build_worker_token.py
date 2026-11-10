from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser

from django_spec_mcp.auth.types.token_info import TokenInfo
from django_spec_mcp.tasks.types.task_record import TaskRecord


def build_worker_token(record: TaskRecord) -> TokenInfo:
    """Rebuild the authorization context a task was created under."""
    return TokenInfo(
        user=_user(record.user_pk),
        scopes=record.scopes,
        audience=record.audience,
    )


def _user(user_pk: Any) -> Any:
    if user_pk is None:
        return AnonymousUser()
    model = get_user_model()
    try:
        user: Any = model._default_manager.get(pk=user_pk)
    except model.DoesNotExist:
        return AnonymousUser()
    # ``getattr`` default: a custom user model need not carry the flag.
    if not getattr(user, "is_active", True):
        return AnonymousUser()
    return user


__all__ = ["build_worker_token"]
