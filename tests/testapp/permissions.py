from __future__ import annotations

from typing import Any

from django_service_specs import PermissionCheck


class IsAuthenticated(PermissionCheck):
    """Grant an authenticated principal only."""

    def has_permission(self, principal: Any, spec: Any) -> bool:
        return bool(principal.is_authenticated)
