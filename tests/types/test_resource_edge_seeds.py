"""``RESOURCE_EDGE_SEEDS``: what a resource read seeds beyond the kernel's set."""

from __future__ import annotations

from django_service_specs import RESERVED_POOL_SEEDS

from django_spec_mcp.types.resource_edge_seeds import RESOURCE_EDGE_SEEDS


def test_the_resource_edge_seeds_are_what_the_kernel_leaves_out() -> None:
    assert frozenset({"request"}) == RESOURCE_EDGE_SEEDS
    assert not (RESOURCE_EDGE_SEEDS & RESERVED_POOL_SEEDS)
