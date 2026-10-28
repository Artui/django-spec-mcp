"""``COMPLETION_EDGE_SEEDS``, with nothing of drf-services in the subject.

Its value is pinned against drf-services' ``DROPPED_EDGE_SEEDS`` in
``test_completion_edge_seeds_drfs.py``; this asks what it is to this package.
"""

from __future__ import annotations

from django_service_specs import RESERVED_POOL_SEEDS

from django_spec_mcp.types import COMPLETION_EDGE_SEEDS


def test_the_completion_edge_seeds_are_request_and_serializer_and_no_kernel_seed() -> None:
    assert frozenset({"request", "serializer"}) == COMPLETION_EDGE_SEEDS
    assert not (COMPLETION_EDGE_SEEDS & RESERVED_POOL_SEEDS)
