"""``INTERNAL_ERROR_MESSAGE`` - the whole message a client reads when a dispatch raised instead of returning."""

from __future__ import annotations

INTERNAL_ERROR_MESSAGE: str = "Internal error"
"""The whole message a client reads when a dispatch raised instead of returning.

JSON-RPC's own wording for ``-32603``, and deliberately nothing more: an
exception's text is written for whoever reads the traceback, not for a client,
and it can carry a validator's detail, a SQL fragment or a file path. The
transport logs the exception with its traceback at the same moment, so the
detail an operator needs is in the log, keyed by the request id both sides
share. Used by both viewsets' backstop and by the in-stream error of a
progress-carrying response, so the two delivery modes answer one failure the
same way.
"""
