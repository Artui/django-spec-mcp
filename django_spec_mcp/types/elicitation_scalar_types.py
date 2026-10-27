"""``ELICITATION_SCALAR_TYPES`` - the JSON-Schema type values a form field may declare."""

from __future__ import annotations

ELICITATION_SCALAR_TYPES: frozenset[str] = frozenset({"string", "number", "integer", "boolean"})
"""The JSON-Schema ``type`` values a form field may declare.

``requestedSchema`` is a **restricted** subset: *"Only top-level properties are
allowed, without nesting."* ``object`` is therefore never valid, and ``array``
only in the multi-select-enum shape — which is why that one is checked
separately rather than being a fourth member here."""
