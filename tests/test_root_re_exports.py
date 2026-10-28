"""Names this package reads from the kernel, pinned to their shape.

``RESERVED_POOL_SEEDS`` is the kernel's and is read from it directly. It is
pinned to its full shape here, so an upstream change fails a test in this
package rather than moving a name it uses without a word.
"""

from __future__ import annotations

from django_service_specs import RESERVED_POOL_SEEDS


def test_reserved_pool_seeds_shape() -> None:
    # Re-exported from django-service-specs, which owns the set. The local copy
    # this once replaced had fallen a key behind it (``collection``) — pinning the
    # shape here is what would have caught that, so keep asserting the full set
    # rather than deferring to the import. drf-services 0.58.0 ("Breaking: pool
    # seeds") swapped ``request`` and ``serializer`` for ``result`` and
    # ``queryset``: the DRF edge seeds ``request`` itself, which is why
    # ``RESOURCE_EDGE_SEEDS`` exists. The kernel's 0.14.0 reserves
    # ``call_context``, the one dict a call hands to every stage naming it, and
    # its 0.16.0 reserves ``shapes``, the arguments a call sends for the spec's
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
