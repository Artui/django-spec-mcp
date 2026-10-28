"""``COMPLETION_EDGE_SEEDS`` - sibling names a completion drops, beyond the kernel's reserved set."""

from __future__ import annotations

COMPLETION_EDGE_SEEDS: frozenset[str] = frozenset({"request", "serializer"})
"""Sibling names ``completion/complete`` drops, beyond the kernel's reserved set.

A completer is dispatched by name from a pool built of the client's resolved
siblings with the transport's seeds over them, and every name the rest of the
package treats as filled by the server is dropped from those siblings first,
so no client-chosen value binds to a parameter of that name. The kernel's
``RESERVED_POOL_SEEDS`` is one half. This is the other: ``request``, which the
completion seeds itself, and ``serializer``, which a tool's validator binds and
the tool paths drop from a caller's arguments. Neither is in the kernel's set,
which leaves ``request`` to the transport that has one.

Written out here rather than read from drf-services, whose ``DROPPED_EDGE_SEEDS``
it replaces, and pinned equal to that set by
``test_the_completion_edge_seeds_are_what_drf_services_drops``, so the two
cannot drift while both exist.
``test_a_sibling_named_as_a_name_drf_services_drops_never_reaches_the_completer``
holds ``serializer``; a sibling named ``request`` is outranked by the seed
written over it whether or not it is dropped first.
"""
