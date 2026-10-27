"""``TASKS_EXTENSION_ID`` - identifier for the tasks extension, in both directions."""

from __future__ import annotations

TASKS_EXTENSION_ID: str = "io.modelcontextprotocol/tasks"
"""Identifier for the tasks extension, in both directions.

Unlike ``UI_EXTENSION_ID`` this one is symmetric: the client declares it
under ``clientCapabilities.extensions`` on **every** request and the server
under ``capabilities.extensions`` in ``server/discover``, with an empty settings
object each way. The client's declaration is per request, and a server **MUST
NOT** answer with a task "regardless of prior declarations".
"""
