"""Kernel permission checks the test mounts and suites share."""

from __future__ import annotations

from typing import Any

from django_service_specs import PermissionCheck


class IsAuthenticated(PermissionCheck):
    """Grant an authenticated principal only.

    A kernel ``PermissionCheck`` refusing raises ``NotPermitted``, which the
    wire answers as ``-32006`` with HTTP 403.
    """

    def has_permission(self, principal: Any, spec: Any) -> bool:
        return bool(principal.is_authenticated)
