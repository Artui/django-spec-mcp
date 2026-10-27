"""The ``scopes=`` a kernel tool or resource is registered with, as the gate holds them."""

from __future__ import annotations

from collections.abc import Sequence

from django.core.exceptions import ImproperlyConfigured


def kernel_scopes(label: str, scopes: str | Sequence[str] | None) -> tuple[str, ...] | None:
    """``scopes`` as a tuple of scope names, or ``None`` for no scope gate.

    A bare string is the single-scope case, as ``ScopeRequired`` reads it:
    ``tuple("billing")`` would be seven one-character scopes no token carries
    (``test_a_bare_string_scope_is_one_scope``). An empty sequence is refused,
    since it reads as a gate and admits every caller. Each half of that guard
    is held by a test: ``test_an_empty_scope_tuple_is_refused`` fails without
    the emptiness test, and ``test_scopes_become_the_scope_gate_and_none_is_no_gate``
    without the ``None`` one.
    """
    if isinstance(scopes, str):
        return (scopes,)
    if scopes is not None and not scopes:
        raise ImproperlyConfigured(
            f"{label}: scopes=() permits every caller while reading as a gate. "
            "Name at least one scope, or leave scopes=None for no scope gate."
        )
    return None if scopes is None else tuple(scopes)


__all__ = ["kernel_scopes"]
