"""``RESOURCE_EDGE_SEEDS`` - pool keys resources/read seeds itself, beyond the kernel's reserved set."""

from __future__ import annotations

RESOURCE_EDGE_SEEDS: frozenset[str] = frozenset({"request"})
"""Pool keys ``resources/read`` seeds itself, beyond the kernel's reserved set.

``RESERVED_POOL_SEEDS`` is the kernel's, and since drf-services 0.58.0 it no
longer lists ``request``: off HTTP there is none, so the kernel leaves it to an
HTTP adapter to seed. ``resources/read`` does, laying the synthesised request
over the URI-template variables, so a variable named ``request`` would never
reach the selector. ``register_resource`` reserves these beside the kernel's
set and the server's ``pool_seeds`` so such a template is refused rather than
silently shadowed
(``test_a_template_variable_named_after_a_pool_seed_is_refused[request]``).
"""
