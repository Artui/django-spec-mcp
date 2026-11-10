from __future__ import annotations

from django_service_specs import CALL_SEEDS, RESERVED_POOL_SEEDS


def test_reserved_pool_seeds_shape() -> None:
    # Read from django-service-specs, which owns the set. A copy kept here would
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
    assert frozenset({"request", "view"}) == CALL_SEEDS
