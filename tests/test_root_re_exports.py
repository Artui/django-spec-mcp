"""Names this package reads from the kernel, pinned to their shape.

``RESERVED_POOL_SEEDS`` and ``CALL_SEEDS`` are the kernel's and are read from
it directly. Each is pinned to its full shape here, so an upstream change fails
a test in this package rather than moving a name it uses without a word.
"""

from __future__ import annotations

from django_service_specs import CALL_SEEDS, RESERVED_POOL_SEEDS


def test_reserved_pool_seeds_shape() -> None:
    # Read from django-service-specs, which owns the set. A copy kept here would
    # drift a key behind it, and pinning the full shape is what catches an upstream
    # change, so assert the whole set rather than deferring to the import. ``request``
    # and ``view`` are not in the set: they are the call's own, the kernel's
    # ``CALL_SEEDS``, pinned below. ``call_context`` is the one dict a call hands to
    # every stage naming it, and ``shapes`` the arguments a call sends for the spec's
    # declared shapes.
    assert (
        frozenset(
            {
                "user",
                "data",
                "instance",
                "collection",
                "result",
                "queryset",
                "progress",
                "call_context",
                "shapes",
            }
        )
        == RESERVED_POOL_SEEDS
    )


def test_call_seeds_shape() -> None:
    # What dispatch hands every callable off the call's context. ``register_resource``
    # reserves both beside ``RESERVED_POOL_SEEDS``, so a URI-template variable of
    # either name is refused rather than standing in for the call's own value.
    assert frozenset({"request", "view"}) == CALL_SEEDS
