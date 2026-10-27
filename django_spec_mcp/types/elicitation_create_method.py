"""``ELICITATION_CREATE_METHOD`` - the one server-to-client request this package ever issues."""

from __future__ import annotations

ELICITATION_CREATE_METHOD: str = "elicitation/create"
"""The one server-to-client request this package ever issues.

**It is not sent as a request.** From ``2026-07-28`` the server-initiated
direction is gone: an ``ElicitRequest`` travels as a *value* inside an
``InputRequiredResult``'s ``inputRequests`` map, and the client answers by
retrying the original call. The method name survives only as the discriminator
naming which kind of input is being asked for. The siblings the spec allows
there, ``sampling/createMessage`` and ``roots/list``, are Deprecated as of this
revision and are not built.
"""
