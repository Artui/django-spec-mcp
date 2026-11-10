from __future__ import annotations

import hashlib
import json
from typing import Any


def fingerprint_request(method: str, payload: Any) -> str:
    """A stable digest identifying the call a ``requestState`` belongs to."""
    encoded: str = json.dumps(
        {"method": method, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = ["fingerprint_request"]
