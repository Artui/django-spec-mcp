"""``TASK_METHODS`` - the extension's three methods."""

from __future__ import annotations

TASK_METHODS: frozenset[str] = frozenset({"tasks/get", "tasks/update", "tasks/cancel"})
"""The extension's three methods.

There is no ``tasks/list``, and the omission is the spec's: without sessions
there is no principal-scoped view to list *over*, so the only defence against
one caller reading another's work is that task ids are unguessable. Retrieval
is polling ``tasks/get``, paced by ``pollIntervalMs``.
"""
