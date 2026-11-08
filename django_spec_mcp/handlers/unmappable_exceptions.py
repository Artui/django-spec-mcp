from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django_service_specs import NotPermitted, PrincipalUnavailable

UNMAPPABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    NotPermitted,
    PrincipalUnavailable,
    PermissionDenied,
    ImproperlyConfigured,
)
"""What no ``exception_map`` entry answers, however broad, and none may name.

A denial, the kernel's or Django's: its answer is a ``-32006`` under a ``403``, and a
tool result would tell the client the tool ran. A configuration error: it is the
server's own mistake, which a handler's result would hide from the people who can
correct it, and which fails the listing the same way."""


__all__ = ["UNMAPPABLE_EXCEPTIONS"]
