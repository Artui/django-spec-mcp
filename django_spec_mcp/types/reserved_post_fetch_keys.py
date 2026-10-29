"""``RESERVED_POST_FETCH_KEYS`` - keys the post-fetch pipeline of a LIST selector tool consumes."""

from __future__ import annotations

RESERVED_POST_FETCH_KEYS: frozenset[str] = frozenset({"page", "limit"})
"""Keys the post-fetch pipeline of a ``LIST`` selector tool consumes.

Stripped from a ``LIST`` tool's dispatched selector's kwarg pool, which would
otherwise receive kwargs it never declared. A ``RETRIEVE`` tool cannot
paginate, so its selector's pool keeps both, as the Pydantic-AI route's does. Scoped to that pool only: the ``FilterSet``
is handed the arguments unstripped, because it reads the fields it declares, as
it does over HTTP.

**The strip is by name, so every entry here is a name a ``LIST`` selector
cannot use for a parameter of its own.** That is the cost of each one, and the reason the
set should hold only keys the pipeline genuinely consumes. ``page`` and
``limit`` earn it: they are read here and never reach the callable.
"""
