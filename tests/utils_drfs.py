"""Test helpers that build DRF or drf-services objects, kept until the drfs path is deleted."""

from __future__ import annotations

from typing import Any

from rest_framework.permissions import BasePermission


def granting_route(key: str, value: Any, seen: list[dict[str, Any]]) -> type[BasePermission]:
    """A DRF permission granting a route whose ``view.kwargs[key]`` is ``value``.

    Records every ``view.kwargs`` it is shown into ``seen``: a class rather than
    an instance is what a spec declares, and the paths instantiate it at
    different times, so the record lives outside the instance. Reads with
    ``.get`` so a check made against ``{}`` denies rather than raising, which is
    what the routes this stands in for looked like before they were bound.
    """

    class _GrantsOneRoute(BasePermission):
        def has_permission(self, request: Any, view: Any) -> bool:
            seen.append(dict(view.kwargs))
            return view.kwargs.get(key) == value

    return _GrantsOneRoute
